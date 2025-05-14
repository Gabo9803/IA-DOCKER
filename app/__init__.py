from flask import Flask, render_template, request, jsonify
from openai import OpenAI
import psycopg2
from psycopg2 import pool
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Configuración de OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Configuración del pool de conexiones a PostgreSQL
db_pool = pool.SimpleConnectionPool(
    1, 20,
    user=os.getenv("POSTGRES_USER"),
    password=os.getenv("POSTGRES_PASSWORD"),
    host="db",
    port="5432",
    database=os.getenv("POSTGRES_DB")
)

def init_db():
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id SERIAL PRIMARY KEY,
                    user_message TEXT NOT NULL,
                    ai_response TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()
    finally:
        db_pool.putconn(conn)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    user_message = request.json.get('message')
    if not user_message:
        return jsonify({'error': 'No message provided'}), 400

    try:
        # Llamada a la API de OpenAI
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": user_message}]
        )
        ai_response = response.choices[0].message.content

        # Guardar en la base de datos
        conn = db_pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO conversations (user_message, ai_response) VALUES (%s, %s) RETURNING id;",
                    (user_message, ai_response)
                )
                conn.commit()
        finally:
            db_pool.putconn(conn)

        return jsonify({'response': ai_response})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000)