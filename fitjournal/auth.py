"""Account registration, session authentication and CSRF protection."""
import re
import secrets
import sqlite3
import time
from collections import deque
from datetime import timedelta
from threading import Lock

from flask import abort, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

# PBKDF2 keeps memory usage within the 96 MiB Play2Go allocation.
HASH_METHOD = 'pbkdf2:sha256:1000000'


def register_auth(app, db):
    app.permanent_session_lifetime = timedelta(days=7)
    attempts = deque()
    attempt_lock = Lock()
    dummy_hash = generate_password_hash('invalid-account-dummy', method=HASH_METHOD)

    @app.before_request
    def authenticate():
        user_id = session.get('user_id')
        g.user = db().execute('SELECT id,username FROM users WHERE id=?', (user_id,)).fetchone() if isinstance(user_id, int) else None
        if request.method == 'POST':
            token = request.form.get('csrf', '')
            if not secrets.compare_digest(token.encode(), session.get('csrf', '!').encode()):
                abort(400)
        if request.endpoint not in ('login', 'register', 'static') and g.user is None:
            return redirect(url_for('login'))

    @app.after_request
    def response_headers(response):
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        return response

    def limit_attempts():
        # Process-wide cap cannot be bypassed by spoofing forwarding headers.
        # The deployment runs a single Waitress process.
        with attempt_lock:
            now = time.monotonic()
            while attempts and attempts[0] < now - 300:
                attempts.popleft()
            if len(attempts) >= 30:
                abort(429, description='Слишком много попыток. Повтори через 5 минут.')
            attempts.append(now)

    def sign_in(user_id):
        session.clear()
        session['user_id'] = user_id
        session.permanent = True

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if g.user is not None:
            return redirect(url_for('index'))
        error, username = None, ''
        if request.method == 'POST':
            limit_attempts()
            username = request.form.get('username', '').strip().casefold()
            password = request.form.get('password', '')
            confirmation = request.form.get('confirm', '')
            if not re.fullmatch(r'[a-z0-9_]{3,32}', username):
                error = 'Логин должен содержать 3–32 символа: латинские буквы, цифры или подчёркивание.'
            elif not 12 <= len(password) <= 128:
                error = 'Пароль должен содержать от 12 до 128 символов.'
            elif password != confirmation:
                error = 'Пароли не совпадают.'
            else:
                password_hash = generate_password_hash(password, method=HASH_METHOD)
                try:
                    with db():
                        result = db().execute('INSERT INTO users (username,password_hash) VALUES (?,?)', (username,password_hash))
                        user_id = result.lastrowid
                        db().execute('INSERT INTO user_settings VALUES (?,?,?,?)',(user_id,username,2900,165))
                    sign_in(user_id)
                    return redirect(url_for('index'))
                except sqlite3.IntegrityError:
                    error = 'Этот логин уже занят.'
        return render_template('auth.html', register=True, error=error, username=username), 422 if error else 200

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if g.user is not None:
            return redirect(url_for('index'))
        error, username = None, ''
        if request.method == 'POST':
            limit_attempts()
            username = request.form.get('username', '').strip().casefold()
            password = request.form.get('password', '')
            if len(password) > 128:
                error = 'Неверный логин или пароль.'
            else:
                account = db().execute('SELECT id,password_hash FROM users WHERE username=?',(username,)).fetchone()
                valid = check_password_hash(account['password_hash'] if account else dummy_hash,password)
                if account and valid:
                    sign_in(account['id'])
                    return redirect(url_for('index'))
                error = 'Неверный логин или пароль.'
        return render_template('auth.html', register=False, error=error, username=username), 422 if error else 200

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))
