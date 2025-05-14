from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash, send_from_directory
from flask_socketio import SocketIO, emit
from openai import OpenAI
import psycopg2
from psycopg2 import pool
import os
from dotenv import load_dotenv
import bcrypt
import re
import redis
import json
import base64
import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from langdetect import detect, DetectorFactory
from werkzeug.utils import secure_filename

load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)
socketio = SocketIO(app)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)

# Database connection using DATABASE_URL
DATABASE_URL = os.getenv("DATABASE_URL")
db_pool = pool.SimpleConnectionPool(
    1, 20,
    dsn=DATABASE_URL
)

# Ephemeral storage for file uploads (free tier)
UPLOAD_FOLDER = '/tmp/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'txt', 'jpg', 'jpeg', 'png'}  # Removed 'pdf' for free tier

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def init_db():
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(50) UNIQUE NOT NULL,
                    password BYTEA NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id),
                    avatar VARCHAR(255),
                    bio TEXT
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    user_message TEXT NOT NULL,
                    ai_response TEXT NOT NULL,
                    file_url VARCHAR(255),
                    file_name VARCHAR(100),
                    avatar VARCHAR(255),
                    edited BOOLEAN DEFAULT FALSE,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id),
                    model VARCHAR(50) DEFAULT 'gpt-3.5-turbo',
                    tone VARCHAR(50) DEFAULT 'formal',
                    language VARCHAR(10) DEFAULT 'auto'
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS conversation_context (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    key VARCHAR(100) NOT NULL,
                    value TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    description TEXT NOT NULL,
                    scheduled_time TIMESTAMP NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS achievements (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    name VARCHAR(100) NOT NULL,
                    description TEXT NOT NULL,
                    achieved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()
    finally:
        db_pool.putconn(conn)

def extract_context(message):
    context = {}
    names = re.findall(r'\b[A-Z][a-z]*\b', message)
    if names:
        context['names'] = list(set(names))
    dates = re.findall(r'\b\d{1,2}/\d{1,2}/\d{4}\b|\b(hoy|mañana|ayer)\b', message)
    if dates:
        context['dates'] = list(set([d[0] or d[1] for d in dates]))
    return context

def check_achievements(user_id):
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM conversations WHERE user_id = %s", (user_id,))
            message_count = cur.fetchone()[0]
            achievements = []
            if message_count >= 100 and not achievement_exists(user_id, "Cien Mensajes"):
                cur.execute(
                    "INSERT INTO achievements (user_id, name, description) VALUES (%s, %s, %s) RETURNING id",
                    (user_id, "Cien Mensajes", "Enviados 100 mensajes")
                )
                achievements.append({"name": "Cien Mensajes", "description": "Enviados 100 mensajes"})
            if message_count >= 10 and not achievement_exists(user_id, "Primeros Pasos"):
                cur.execute(
                    "INSERT INTO achievements (user_id, name, description) VALUES (%s, %s, %s) RETURNING id",
                    (user_id, "Primeros Pasos", "Enviados 10 mensajes")
                )
                achievements.append({"name": "Primeros Pasos", "description": "Enviados 10 mensajes"})
            conn.commit()
            return achievements
    finally:
        db_pool.putconn(conn)

def achievement_exists(user_id, name):
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM achievements WHERE user_id = %s AND name = %s", (user_id, name))
            return cur.fetchone() is not None
    finally:
        db_pool.putconn(conn)

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        conn = db_pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, password FROM users WHERE username = %s", (username,))
                user = cur.fetchone()
                if user and bcrypt.checkpw(password.encode('utf-8'), user[1]):
                    session['user_id'] = user[0]
                    session['username'] = username
                    return redirect(url_for('index'))
                flash('Usuario o contraseña incorrectos', 'error')
        finally:
            db_pool.putconn(conn)
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if len(password) < 6:
            flash('La contraseña debe tener al menos 6 caracteres', 'error')
            return render_template('register.html')
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
        conn = db_pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE username = %s", (username,))
                if cur.fetchone():
                    flash('El usuario ya existe', 'error')
                else:
                    cur.execute("INSERT INTO users (username, password) VALUES (%s, %s) RETURNING id",
                               (username, hashed_password))
                    user_id = cur.fetchone()[0]
                    cur.execute("INSERT INTO user_preferences (user_id) VALUES (%s)", (user_id,))
                    cur.execute("INSERT INTO user_profiles (user_id) VALUES (%s)", (user_id,))
                    conn.commit()
                    session['user_id'] = user_id
                    session['username'] = username
                    return redirect(url_for('index'))
        except psycopg2.IntegrityError:
            flash('Error al registrar el usuario', 'error')
        finally:
            db_pool.putconn(conn)
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/history', methods=['GET'])
def history():
    if 'user_id' not in session:
        return jsonify({'error': 'No autenticado'}), 401
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT c.id, c.user_message, c.ai_response, c.timestamp, c.edited, c.file_url, c.file_name, p.avatar "
                "FROM conversations c JOIN user_profiles p ON c.user_id = p.user_id "
                "WHERE c.user_id = %s ORDER BY c.timestamp ASC",
                (session['user_id'],)
            )
            messages = [{'id': row[0], 'user_message': row[1], 'ai_response': row[2], 'timestamp': row[3].strftime('%H:%M:%S'), 'edited': row[4], 'file_url': row[5], 'file_name': row[6], 'avatar': row[7]} for row in cur.fetchall()]
        return jsonify(messages)
    finally:
        db_pool.putconn(conn)

@app.route('/preferences', methods=['GET', 'POST'])
def preferences():
    if 'user_id' not in session:
        return jsonify({'error': 'No autenticado'}), 401
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            if request.method == 'POST':
                model = request.form.get('model')
                tone = request.form.get('tone')
                language = request.form.get('language')
                bio = request.form.get('bio')
                profile_picture = request.files.get('profile_picture')

                if model not in ['gpt-3.5-turbo', 'gpt-4o'] or tone not in ['formal', 'informal', 'humorístico', 'técnico']:
                    return jsonify({'error': 'Preferencias inválidas'}), 400

                avatar_url = None
                if profile_picture and allowed_file(profile_picture.filename):
                    filename = secure_filename(profile_picture.filename)
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    profile_picture.save(filepath)
                    avatar_url = f"/static/uploads/{filename}"

                cur.execute(
                    "INSERT INTO user_preferences (user_id, model, tone, language) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (user_id) DO UPDATE SET model = EXCLUDED.model, tone = EXCLUDED.tone, language = EXCLUDED.language",
                    (session['user_id'], model, tone, language)
                )
                cur.execute(
                    "INSERT INTO user_profiles (user_id, avatar, bio) "
                    "VALUES (%s, %s, %s) "
                    "ON CONFLICT (user_id) DO UPDATE SET avatar = COALESCE(EXCLUDED.avatar, user_profiles.avatar), bio = EXCLUDED.bio",
                    (session['user_id'], avatar_url, bio)
                )
                conn.commit()
                return jsonify({'success': 'Preferencias guardadas', 'avatar': avatar_url})
            else:
                cur.execute(
                    "SELECT model, tone, language FROM user_preferences WHERE user_id = %s",
                    (session['user_id'],)
                )
                prefs = cur.fetchone()
                cur.execute(
                    "SELECT avatar, bio FROM user_profiles WHERE user_id = %s",
                    (session['user_id'],)
                )
                profile = cur.fetchone()
                return jsonify({
                    'model': prefs[0] if prefs else 'gpt-3.5-turbo',
                    'tone': prefs[1] if prefs else 'formal',
                    'language': prefs[2] if prefs else 'auto',
                    'avatar': profile[0] if profile else None,
                    'bio': profile[1] if profile else ''
                })
    finally:
        db_pool.putconn(conn)

@app.route('/chat', methods=['POST'])
def chat():
    if 'user_id' not in session:
        return jsonify({'error': 'No autenticado'}), 401

    message = request.form.get('message', '')
    file = request.files.get('file')
    file_url = None
    file_name = None

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        file_url = f"/static/uploads/{filename}"
        file_name = filename
        if file.mimetype.startswith('text'):
            with open(filepath, 'r', encoding='utf-8') as f:
                message += f"\nArchivo: {f.read()}"
        elif file.mimetype.startswith('image'):
            with open(filepath, 'rb') as f:
                encoded_image = base64.b64encode(f.read()).decode('utf-8')
                message += f"\n[Imagen: {filename}]"
        # Note: Files in /tmp/uploads are ephemeral in Render free tier

    if not message.strip() and not file:
        return jsonify({'error': 'Mensaje vacío'}), 400

    cache_key = f"chat:{session['user_id']}:{hash(message)}"
    cached_response = redis_client.get(cache_key)
    if cached_response:
        return json.loads(cached_response)

    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT model, tone, language FROM user_preferences WHERE user_id = %s",
                (session['user_id'],)
            )
            prefs = cur.fetchone()
            model = prefs[0] if prefs else 'gpt-3.5-turbo'
            tone = prefs[1] if prefs else 'formal'
            language = prefs[2] if prefs else 'auto'

            detected_lang = detect(message) if message.strip() and language == 'auto' else language
            lang_map = {'es': 'Español', 'en': 'Inglés', 'fr': 'Francés'}
            target_lang = lang_map.get(detected_lang, 'Español')

            cur.execute(
                "SELECT user_message, ai_response FROM conversations WHERE user_id = %s ORDER BY timestamp DESC LIMIT 5",
                (session['user_id'],)
            )
            history = [{'role': 'user', 'content': row[0]} if i % 2 == 0 else {'role': 'assistant', 'content': row[1]} for i, row in enumerate(cur.fetchall())]

            context = extract_context(message)
            for key, value in context.items():
                cur.execute(
                    "INSERT INTO conversation_context (user_id, key, value) VALUES (%s, %s, %s)",
                    (session['user_id'], key, json.dumps(value))
                )

            cur.execute(
                "SELECT key, value FROM conversation_context WHERE user_id = %s ORDER BY timestamp DESC LIMIT 10",
                (session['user_id'],)
            )
            context_data = {row[0]: json.loads(row[1]) for row in cur.fetchall()}
            context_str = "\n".join(f"{k}: {v}" for k, v in context_data.items())

            prompt = f"Eres un asistente útil que responde en un tono {tone} en {target_lang}. Contexto: {context_str}\nUsuario: {message}"
            messages = [
                {"role": "system", "content": "Eres un asistente útil que responde de manera clara y precisa."},
                *history,
                {"role": "user", "content": prompt}
            ]

            if file and file.mimetype.startswith('image'):
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": message},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded_image}"}}
                    ]
                })

            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=500,
                    temperature=0.7
                )
                ai_response = response.choices[0].message.content
            except Exception as e:
                return jsonify({'error': f'Error en la API de OpenAI: {str(e)}'}), 500

            cur.execute(
                "SELECT avatar FROM user_profiles WHERE user_id = %s",
                (session['user_id'],)
            )
            avatar = cur.fetchone()[0]

            cur.execute(
                "INSERT INTO conversations (user_id, user_message, ai_response, file_url, file_name, avatar) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (session['user_id'], message, ai_response, file_url, file_name, avatar)
            )
            conn.commit()

            quick_replies = ["Cuéntame más", "Explica en detalle", "¿Puedes dar un ejemplo?"]
            response_data = {'response': ai_response, 'quick_replies': quick_replies}
            redis_client.setex(cache_key, 3600, json.dumps(response_data))

            achievements = check_achievements(session['user_id'])
            if achievements:
                socketio.emit('achievement', achievements, to=str(session['user_id']))

            return jsonify(response_data)
    finally:
        db_pool.putconn(conn)

@app.route('/static/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/edit_message', methods=['POST'])
def edit_message():
    if 'user_id' not in session:
        return jsonify({'error': 'No autenticado'}), 401
    data = request.get_json()
    message_id = data.get('message_id')
    new_message = data.get('new_message')
    if not message_id or not new_message:
        return jsonify({'error': 'Datos inválidos'}), 400
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE conversations SET user_message = %s, edited = TRUE "
                "WHERE id = %s AND user_id = %s RETURNING id",
                (new_message, message_id, session['user_id'])
            )
            if cur.fetchone():
                conn.commit()
                return jsonify({'success': 'Mensaje editado'})
            return jsonify({'error': 'Mensaje no encontrado o no autorizado'}), 404
    finally:
        db_pool.putconn(conn)

@app.route('/delete_message', methods=['POST'])
def delete_message():
    if 'user_id' not in session:
        return jsonify({'error': 'No autenticado'}), 401
    data = request.get_json()
    message_id = data.get('message_id')
    if not message_id:
        return jsonify({'error': 'Datos inválidos'}), 400
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM conversations WHERE id = %s AND user_id = %s RETURNING id",
                (message_id, session['user_id'])
            )
            if cur.fetchone():
                conn.commit()
                return jsonify({'success': 'Mensaje eliminado'})
            return jsonify({'error': 'Mensaje no encontrado o no autorizado'}), 404
    finally:
        db_pool.putconn(conn)

@app.route('/tasks', methods=['GET', 'POST'])
def tasks():
    if 'user_id' not in session:
        return jsonify({'error': 'No autenticado'}), 401
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            if request.method == 'POST':
                description = request.form.get('description')
                scheduled_time = request.form.get('scheduled_time')  # Format: YYYY-MM-DD HH:MM
                if not description or not scheduled_time:
                    return jsonify({'error': 'Datos inválidos'}), 400
                try:
                    scheduled_time = datetime.datetime.strptime(scheduled_time, '%Y-%m-%d %H:%M')
                except ValueError:
                    return jsonify({'error': 'Formato de fecha inválido'}), 400
                cur.execute(
                    "INSERT INTO tasks (user_id, description, scheduled_time) VALUES (%s, %s, %s)",
                    (session['user_id'], description, scheduled_time)
                )
                conn.commit()
                return jsonify({'success': 'Tarea programada'})
            else:
                cur.execute(
                    "SELECT id, description, scheduled_time FROM tasks WHERE user_id = %s",
                    (session['user_id'],)
                )
                tasks = [{'id': row[0], 'description': row[1], 'scheduled_time': row[2].strftime('%Y-%m-%d %H:%M')} for row in cur.fetchall()]
                return jsonify(tasks)
    finally:
        db_pool.putconn(conn)

@app.route('/delete_task', methods=['POST'])
def delete_task():
    if 'user_id' not in session:
        return jsonify({'error': 'No autenticado'}), 401
    data = request.get_json()
    task_id = data.get('task_id')
    if not task_id:
        return jsonify({'error': 'Datos inválidos'}), 400
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM tasks WHERE id = %s AND user_id = %s RETURNING id",
                (task_id, session['user_id'])
            )
            if cur.fetchone():
                conn.commit()
                return jsonify({'success': 'Tarea eliminada'})
            return jsonify({'error': 'Tarea no encontrada o no autorizada'}), 404
    finally:
        db_pool.putconn(conn)

@app.route('/achievements', methods=['GET'])
def achievements():
    if 'user_id' not in session:
        return jsonify({'error': 'No autenticado'}), 401
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, description, achieved_at FROM achievements WHERE user_id = %s ORDER BY achieved_at DESC",
                (session['user_id'],)
            )
            achievements = [{'name': row[0], 'description': row[1], 'achieved_at': row[2].strftime('%Y-%m-%d %H:%M')} for row in cur.fetchall()]
            return jsonify(achievements)
    finally:
        db_pool.putconn(conn)

@socketio.on('connect')
def handle_connect():
    if 'user_id' in session:
        emit('user_connected', {'user_id': session['user_id'], 'username': session['username']}, broadcast=True)

if __name__ == '__main__':
    init_db()
    socketio.run(app, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)