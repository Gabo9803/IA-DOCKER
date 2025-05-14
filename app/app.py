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
import logging
import shutil
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Ensure consistent language detection
DetectorFactory.seed = 0

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", os.urandom(24))
socketio = SocketIO(app, cors_allowed_origins="*")  # Allow HTTPS for Render

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)

# Database connection using DATABASE_URL
DATABASE_URL = os.getenv("DATABASE_URL")
try:
    db_pool = pool.SimpleConnectionPool(
        1, 10,  # Reduced max connections for free tier
        dsn=DATABASE_URL
    )
    logger.info("Database pool initialized")
except Exception as e:
    logger.error(f"Failed to initialize database pool: {e}")
    raise

# Ephemeral storage for file uploads
UPLOAD_FOLDER = '/tmp/uploads'
try:
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    logger.info(f"Upload folder created: {UPLOAD_FOLDER}")
except Exception as e:
    logger.error(f"Failed to create upload folder: {e}")
    raise
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'txt', 'jpg', 'jpeg', 'png'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def clean_upload_folder():
    """Clean old files from upload folder (older than 24 hours)."""
    try:
        now = time.time()
        for filename in os.listdir(app.config['UPLOAD_FOLDER']):
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            if os.path.isfile(file_path) and (now - os.path.getmtime(file_path)) > 24 * 3600:
                os.remove(file_path)
                logger.info(f"Cleaned old file: {filename}")
    except Exception as e:
        logger.error(f"Failed to clean upload folder: {e}")

def notify_task(user_id, task_id, description):
    """Send task notification via SocketIO."""
    try:
        conn = db_pool.getconn()
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM tasks WHERE id = %s AND user_id = %s RETURNING id",
                (task_id, user_id)
            )
            if cur.fetchone():
                conn.commit()
                socketio.emit('task_notification', {
                    'user_id': user_id,
                    'description': description,
                    'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
                }, to=str(user_id))
                logger.info(f"Task {task_id} notified and deleted for user_id: {user_id}")
    except Exception as e:
        logger.error(f"Failed to notify task {task_id}: {e}")
    finally:
        db_pool.putconn(conn)

def schedule_tasks():
    """Schedule all pending tasks from the database."""
    try:
        conn = db_pool.getconn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, user_id, description, scheduled_time FROM tasks WHERE scheduled_time > CURRENT_TIMESTAMP"
            )
            tasks = cur.fetchall()
            scheduler = BackgroundScheduler()
            for task_id, user_id, description, scheduled_time in tasks:
                scheduler.add_job(
                    notify_task,
                    'date',
                    run_date=scheduled_time,
                    args=[user_id, task_id, description]
                )
            scheduler.start()
            logger.info("Scheduled pending tasks")
    except Exception as e:
        logger.error(f"Failed to schedule tasks: {e}")
    finally:
        db_pool.putconn(conn)

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
            logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise
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
    except Exception as e:
        logger.error(f"Failed to check achievements: {e}")
        return []
    finally:
        db_pool.putconn(conn)

def achievement_exists(user_id, name):
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM achievements WHERE user_id = %s AND name = %s", (user_id, name))
            return cur.fetchone() is not None
    except Exception as e:
        logger.error(f"Failed to check achievement existence: {e}")
        return False
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
                    logger.info(f"User {username} logged in")
                    return redirect(url_for('index'))
                flash('Usuario o contraseña incorrectos', 'error')
                logger.warning(f"Failed login attempt for username: {username}")
        except Exception as e:
            logger.error(f"Login error: {e}")
            flash('Error al iniciar sesión', 'error')
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
            logger.warning("Registration failed: Password too short")
            return render_template('register.html')
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
        conn = db_pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE username = %s", (username,))
                if cur.fetchone():
                    flash('El usuario ya existe', 'error')
                    logger.warning(f"Registration failed: Username {username} already exists")
                else:
                    cur.execute("INSERT INTO users (username, password) VALUES (%s, %s) RETURNING id",
                               (username, hashed_password))
                    user_id = cur.fetchone()[0]
                    cur.execute("INSERT INTO user_preferences (user_id) VALUES (%s)", (user_id,))
                    cur.execute("INSERT INTO user_profiles (user_id) VALUES (%s)", (user_id,))
                    conn.commit()
                    session['user_id'] = user_id
                    session['username'] = username
                    logger.info(f"User {username} registered successfully")
                    return redirect(url_for('index'))
        except psycopg2.IntegrityError:
            flash('Error al registrar el usuario', 'error')
            logger.error("Registration failed: Integrity error")
        finally:
            db_pool.putconn(conn)
    return render_template('register.html')

@app.route('/logout')
def logout():
    username = session.get('username', 'unknown')
    session.pop('user_id', None)
    session.pop('username', None)
    logger.info(f"User {username} logged out")
    return redirect(url_for('login'))

@app.route('/history', methods=['GET'])
def history():
    if 'user_id' not in session:
        logger.warning("Unauthorized access attempt to /history")
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
            messages = [{
                'id': row[0],
                'user_message': row[1],
                'ai_response': row[2],
                'timestamp': row[3].strftime('%H:%M:%S'),
                'edited': row[4],
                'file_url': row[5],
                'file_name': row[6],
                'avatar': row[7]
            } for row in cur.fetchall()]
            logger.info(f"Retrieved chat history for user_id: {session['user_id']}")
            return jsonify(messages)
    except Exception as e:
        logger.error(f"Failed to retrieve history: {e}")
        return jsonify({'error': 'Error al recuperar el historial'}), 500
    finally:
        db_pool.putconn(conn)

@app.route('/preferences', methods=['GET', 'POST'])
def preferences():
    if 'user_id' not in session:
        logger.warning("Unauthorized access attempt to /preferences")
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
                    logger.warning(f"Invalid preferences submitted: model={model}, tone={tone}")
                    return jsonify({'error': 'Preferencias inválidas'}), 400

                avatar_url = None
                if profile_picture and allowed_file(profile_picture.filename):
                    filename = secure_filename(profile_picture.filename)
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    try:
                        profile_picture.save(filepath)
                        avatar_url = f"/static/uploads/{filename}"
                        logger.info(f"Profile picture uploaded: {filename}")
                    except Exception as e:
                        logger.error(f"Failed to save profile picture: {e}")
                        return jsonify({'error': 'Error al subir la imagen'}), 500

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
                logger.info(f"Preferences updated for user_id: {session['user_id']}")
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
    except Exception as e:
        logger.error(f"Failed to handle preferences: {e}")
        return jsonify({'error': 'Error al procesar preferencias'}), 500
    finally:
        db_pool.putconn(conn)

@app.route('/chat', methods=['POST'])
def chat():
    if 'user_id' not in session:
        logger.warning("Unauthorized access attempt to /chat")
        return jsonify({'error': 'No autenticado'}), 401

    message = request.form.get('message', '')
    file = request.files.get('file')
    file_url = None
    file_name = None
    upload_warning = "Nota: Los archivos subidos son temporales y pueden eliminarse al reiniciar el servidor en el plan gratuito."

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        try:
            file.save(filepath)
            file_url = f"/static/uploads/{filename}"
            file_name = filename
            logger.info(f"File uploaded: {filename}")
            if file.mimetype.startswith('text'):
                with open(filepath, 'r', encoding='utf-8') as f:
                    message += f"\nArchivo: {f.read()}"
            elif file.mimetype.startswith('image'):
                with open(filepath, 'rb') as f:
                    encoded_image = base64.b64encode(f.read()).decode('utf-8')
                    message += f"\n[Imagen: {filename}]"
        except Exception as e:
            logger.error(f"Failed to save file: {e}")
            return jsonify({'error': 'Error al subir el archivo'}), 500

    if not message.strip() and not file:
        logger.warning("Empty message submitted")
        return jsonify({'error': 'Mensaje vacío'}), 400

    cache_key = f"chat:{session['user_id']}:{hash(message)}"
    cached_response = redis_client.get(cache_key)
    if cached_response:
        logger.info(f"Cache hit for user_id: {session['user_id']}, key: {cache_key}")
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
                logger.error(f"OpenAI API error: {e}")
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
            response_data = {
                'response': ai_response,
                'quick_replies': quick_replies,
                'upload_warning': upload_warning if file else None
            }
            redis_client.setex(cache_key, 3600, json.dumps(response_data))
            logger.info(f"Chat response generated for user_id: {session['user_id']}")

            achievements = check_achievements(session['user_id'])
            if achievements:
                socketio.emit('achievement', achievements, to=str(session['user_id']))
                logger.info(f"Achievements awarded for user_id: {session['user_id']}")

            return jsonify(response_data)
    except Exception as e:
        logger.error(f"Chat processing error: {e}")
        return jsonify({'error': 'Error al procesar el mensaje'}), 500
    finally:
        db_pool.putconn(conn)

@app.route('/static/uploads/<filename>')
def uploaded_file(filename):
    try:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    except Exception as e:
        logger.error(f"Failed to serve file {filename}: {e}")
        return jsonify({'error': 'Archivo no encontrado'}), 404

@app.route('/edit_message', methods=['POST'])
def edit_message():
    if 'user_id' not in session:
        logger.warning("Unauthorized access attempt to /edit_message")
        return jsonify({'error': 'No autenticado'}), 401
    data = request.get_json()
    message_id = data.get('message_id')
    new_message = data.get('new_message')
    if not message_id or not new_message:
        logger.warning("Invalid data for edit_message")
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
                logger.info(f"Message {message_id} edited for user_id: {session['user_id']}")
                return jsonify({'success': 'Mensaje editado'})
            logger.warning(f"Message {message_id} not found or unauthorized for user_id: {session['user_id']}")
            return jsonify({'error': 'Mensaje no encontrado o no autorizado'}), 404
    except Exception as e:
        logger.error(f"Failed to edit message: {e}")
        return jsonify({'error': 'Error al editar el mensaje'}), 500
    finally:
        db_pool.putconn(conn)

@app.route('/delete_message', methods=['POST'])
def delete_message():
    if 'user_id' not in session:
        logger.warning("Unauthorized access attempt to /delete_message")
        return jsonify({'error': 'No autenticado'}), 401
    data = request.get_json()
    message_id = data.get('message_id')
    if not message_id:
        logger.warning("Invalid data for delete_message")
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
                logger.info(f"Message {message_id} deleted for user_id: {session['user_id']}")
                return jsonify({'success': 'Mensaje eliminado'})
            logger.warning(f"Message {message_id} not found or unauthorized for user_id: {session['user_id']}")
            return jsonify({'error': 'Mensaje no encontrado o no autorizado'}), 404
    except Exception as e:
        logger.error(f"Failed to delete message: {e}")
        return jsonify({'error': 'Error al eliminar el mensaje'}), 500
    finally:
        db_pool.putconn(conn)

@app.route('/tasks', methods=['GET', 'POST'])
def tasks():
    if 'user_id' not in session:
        logger.warning("Unauthorized access attempt to /tasks")
        return jsonify({'error': 'No autenticado'}), 401
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            if request.method == 'POST':
                description = request.form.get('description')
                scheduled_time = request.form.get('scheduled_time')  # Format: YYYY-MM-DD HH:MM
                if not description or not scheduled_time:
                    logger.warning("Invalid task data submitted")
                    return jsonify({'error': 'Datos inválidos'}), 400
                try:
                    scheduled_time = datetime.datetime.strptime(scheduled_time, '%Y-%m-%d %H:%M')
                    if scheduled_time <= datetime.datetime.now():
                        logger.warning("Task scheduled time is in the past")
                        return jsonify({'error': 'La fecha debe ser futura'}), 400
                except ValueError:
                    logger.warning("Invalid date format for task")
                    return jsonify({'error': 'Formato de fecha inválido'}), 400
                cur.execute(
                    "INSERT INTO tasks (user_id, description, scheduled_time) VALUES (%s, %s, %s) RETURNING id",
                    (session['user_id'], description, scheduled_time)
                )
                task_id = cur.fetchone()[0]
                conn.commit()
                scheduler = BackgroundScheduler()
                scheduler.add_job(
                    notify_task,
                    'date',
                    run_date=scheduled_time,
                    args=[session['user_id'], task_id, description]
                )
                scheduler.start()
                logger.info(f"Task {task_id} scheduled for user_id: {session['user_id']}")
                return jsonify({'success': 'Tarea programada'})
            else:
                cur.execute(
                    "SELECT id, description, scheduled_time FROM tasks WHERE user_id = %s",
                    (session['user_id'],)
                )
                tasks = [{
                    'id': row[0],
                    'description': row[1],
                    'scheduled_time': row[2].strftime('%Y-%m-%d %H:%M')
                } for row in cur.fetchall()]
                logger.info(f"Retrieved tasks for user_id: {session['user_id']}")
                return jsonify(tasks)
    except Exception as e:
        logger.error(f"Failed to handle tasks: {e}")
        return jsonify({'error': 'Error al procesar tareas'}), 500
    finally:
        db_pool.putconn(conn)

@app.route('/delete_task', methods=['POST'])
def delete_task():
    if 'user_id' not in session:
        logger.warning("Unauthorized access attempt to /delete_task")
        return jsonify({'error': 'No autenticado'}), 401
    data = request.get_json()
    task_id = data.get('task_id')
    if not task_id:
        logger.warning("Invalid data for delete_task")
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
                logger.info(f"Task {task_id} deleted for user_id: {session['user_id']}")
                return jsonify({'success': 'Tarea eliminada'})
            logger.warning(f"Task {task_id} not found or unauthorized for user_id: {session['user_id']}")
            return jsonify({'error': 'Tarea no encontrada o no autorizada'}), 404
    except Exception as e:
        logger.error(f"Failed to delete task: {e}")
        return jsonify({'error': 'Error al eliminar la tarea'}), 500
    finally:
        db_pool.putconn(conn)

@app.route('/achievements', methods=['GET'])
def achievements():
    if 'user_id' not in session:
        logger.warning("Unauthorized access attempt to /achievements")
        return jsonify({'error': 'No autenticado'}), 401
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, description, achieved_at FROM achievements WHERE user_id = %s ORDER BY achieved_at DESC",
                (session['user_id'],)
            )
            achievements = [{
                'name': row[0],
                'description': row[1],
                'achieved_at': row[2].strftime('%Y-%m-%d %H:%M')
            } for row in cur.fetchall()]
            logger.info(f"Retrieved achievements for user_id: {session['user_id']}")
            return jsonify(achievements)
    except Exception as e:
        logger.error(f"Failed to retrieve achievements: {e}")
        return jsonify({'error': 'Error al recuperar logros'}), 500
    finally:
        db_pool.putconn(conn)

@socketio.on('connect')
def handle_connect():
    if 'user_id' in session:
        emit('user_connected', {'user_id': session['user_id'], 'username': session['username']}, broadcast=True)
        logger.info(f"WebSocket connected for user_id: {session['user_id']}")

if __name__ == '__main__':
    init_db()
    # Schedule cleanup of upload folder every 24 hours
    scheduler = BackgroundScheduler()
    scheduler.add_job(clean_upload_folder, 'interval', hours=24)
    scheduler.start()
    # Schedule tasks at startup
    schedule_tasks()
    socketio.run(app, host='0.0.0.0', port=int(os.getenv("PORT", 5000)))