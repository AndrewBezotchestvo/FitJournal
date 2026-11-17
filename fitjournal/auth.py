"""Account registration, session authentication and CSRF protection."""
from hashlib import sha256
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
REMEMBER_DAYS = 180


def register_auth(app, db):
    app.permanent_session_lifetime = timedelta(days=REMEMBER_DAYS)
    app.config['SESSION_REFRESH_EACH_REQUEST'] = False
    attempts = deque()
    attempt_lock = Lock()
    dummy_hash = generate_password_hash('invalid-account-dummy', method=HASH_METHOD)

    @app.before_request
    def authenticate():
        token = session.get('device_token')
        g.user = None
        if isinstance(token, str):
            g.user = db().execute(
                'SELECT users.id, users.username FROM device_sessions '
                'JOIN users ON users.id=device_sessions.user_id '
                'WHERE token_hash=? AND expires_at>?',
                (sha256(token.encode()).hexdigest(), int(time.time())),
            ).fetchone()
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

    def sign_in(user_id, remember):
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        expires = now + (REMEMBER_DAYS if remember else 1) * 86400
        with db() as conn:
            conn.execute('DELETE FROM device_sessions WHERE expires_at<=?', (now,))
            conn.execute('INSERT INTO device_sessions VALUES (?,?,?)',
                         (sha256(token.encode()).hexdigest(), user_id, expires))
        session.clear()
        session['device_token'] = token
        session.permanent = remember

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if g.user is not None:
            return redirect(url_for('index'))
        error, username = None, ''
        remember = request.method == 'GET' or request.form.get('remember') == '1'
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
                    sign_in(user_id, remember)
                    return redirect(url_for('index'))
                except sqlite3.IntegrityError:
                    error = 'Этот логин уже занят.'
        return render_template('auth.html', register=True, error=error, username=username, remember=remember), 422 if error else 200

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if g.user is not None:
            return redirect(url_for('index'))
        error, username = None, ''
        remember = request.method == 'GET' or request.form.get('remember') == '1'
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
                    sign_in(account['id'], remember)
                    return redirect(url_for('index'))
                error = 'Неверный логин или пароль.'
        return render_template('auth.html', register=False, error=error, username=username, remember=remember), 422 if error else 200

    @app.post('/logout')
    def logout():
        token = session.get('device_token')
        if isinstance(token, str):
            with db() as conn:
                conn.execute('DELETE FROM device_sessions WHERE token_hash=?',
                             (sha256(token.encode()).hexdigest(),))
        session.clear()
        return redirect(url_for('login'))
