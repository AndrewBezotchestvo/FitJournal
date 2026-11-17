import json
import click
import math
import os
import secrets
import sqlite3
from datetime import date
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for

# field: key, label, type, min, max, required
def f(key, label, kind='text', lo=None, hi=None, required=True):
    return (key, label, kind, lo, hi, required)

day = f('day', 'Дата', 'date')
name = f('name', 'Упражнение')
sets = f('sets', 'Подходы', 'int', 1, 99)
reps = f('reps', 'Повторения', 'int', 1, 999)
load = f('load', 'Рабочий вес, кг', 'num', 0, 1500, False)
note = f('note', 'Заметка', 'memo', required=False)
minutes = f('minutes', 'Минуты', 'num', 0.1, 1440)
kcal = f('kcal', 'Ккал', 'num', 0, 20000)
protein = f('protein', 'Белок, г', 'num', 0, 1500)
TABS = {
    'plan': ('План', [f('weekday', 'День недели', 'weekday', 1, 7),
                     f('kind', 'Вид', 'kind'), f('name', 'Упражнение / кардио'),
                     f('sets','Подходы','int',1,99,False),
                     f('reps','Повторения','int',1,999,False), load,
                     f('minutes','Минуты','num',0.1,1440,False), note]),
    'strength': ('Силовые', [day, name, sets, reps, load, note]),
    'cardio': ('Кардио', [day, f('name','Вид кардио'), minutes,
                         f('distance','Км','num',0,1000,False),
                         f('effort','Усилие от 1 до 10','int',1,10,False), note]),
    'food': ('Питание', [day, f('name','Блюдо / приём пищи'), kcal, protein, note]),
    'body': ('Замеры', [day, f('weight','Вес тела, кг','num',20,700,False),
                       f('waist','Талия, см','num',20,350,False),
                       f('hip','Бёдра, см','num',20,350,False),
                       f('chest','Грудь, см','num',20,350,False), note]),
}
DAYS = ['Понедельник','Вторник','Среда','Четверг','Пятница','Суббота','Воскресенье']
SETTINGS = [f('name','Имя'),f('kcal','Цель, ккал / день','int',500,15000),
            f('protein','Цель, г белка / день','int',1,1000)]


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(MAX_CONTENT_LENGTH=65536, SESSION_COOKIE_SAMESITE='Lax', DATABASE=os.environ.get('FITJOURNAL_DB', str(Path(app.instance_path)/'journal.sqlite3')))
    app.config.update(config or {})
    Path(app.config['DATABASE']).parent.mkdir(parents=True, exist_ok=True)

    def db():
        if 'db' not in g:
            g.db = sqlite3.connect(app.config['DATABASE'], timeout=10)
            g.db.row_factory = sqlite3.Row
            g.db.execute('PRAGMA foreign_keys=ON')
        return g.db

    with app.app_context(), db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
        conn.execute('CREATE TABLE IF NOT EXISTS device_sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at INTEGER NOT NULL)')
        conn.execute('CREATE INDEX IF NOT EXISTS device_sessions_expiry ON device_sessions (expires_at)')
        conn.execute('CREATE TABLE IF NOT EXISTS user_settings (user_id INTEGER PRIMARY KEY REFERENCES users(id), name TEXT NOT NULL, kcal INTEGER NOT NULL, protein INTEGER NOT NULL)')
        sqltypes = {'int':'INTEGER','weekday':'INTEGER','num':'REAL'}
        for table, (_, fields) in TABS.items():
            columns = ', '.join(f'{key} {sqltypes.get(kind,"TEXT")}' for key, _, kind, *_ in fields)
            conn.execute(f'CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY, {columns})')
            if 'user_id' not in {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN user_id INTEGER REFERENCES users(id)')
            conn.execute(f'CREATE INDEX IF NOT EXISTS owner_{table} ON {table} (user_id)')
            order = 'weekday' if table == 'plan' else 'day'
            conn.execute(f'CREATE INDEX IF NOT EXISTS idx_{table} ON {table} ({order})')
        conn.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), name TEXT, kcal INTEGER, protein INTEGER)')
        conn.execute("INSERT OR IGNORE INTO settings VALUES (1,'Мой дневник',2900,165)")
        conn.execute('CREATE TABLE IF NOT EXISTS local_secret (id INTEGER PRIMARY KEY CHECK(id=1), token TEXT)')
        conn.execute('INSERT OR IGNORE INTO local_secret VALUES (1,?)', (secrets.token_hex(32),))
        app.secret_key = app.config.get('SECRET_KEY') or conn.execute('SELECT token FROM local_secret').fetchone()[0]

    conn.close()

    @app.teardown_appcontext
    def close(_):
        conn = g.pop('db', None)
        if conn is not None:
            conn.close()

    from .auth import register_auth
    register_auth(app, db)

    @app.context_processor
    def context():
        return dict(tabs=TABS, days=DAYS, today=date.today().isoformat(),
                    user=getattr(g, 'user', None), csrf=session.setdefault('csrf', secrets.token_urlsafe(32)))

    def validate(fields):
        values = {}
        for key, label, kind, lo, hi, required in fields:
            raw = request.form.get(key,'').strip()
            if not raw:
                if required:
                    raise ValueError(f'Заполни поле «{label}».')
                values[key] = None
                continue
            try:
                if kind in ('int','weekday','num'):
                    val = int(raw) if kind != 'num' else float(raw.replace(',','.'))
                    if not math.isfinite(val) or not lo <= val <= hi:
                        raise ValueError
                elif kind == 'date':
                    val = date.fromisoformat(raw).isoformat()
                    if val != raw or len(raw) != 10:
                        raise ValueError
                elif kind == 'kind':
                    if raw not in ('strength','cardio'):
                        raise ValueError
                    val = raw
                else:
                    if len(raw) > (2000 if kind == 'memo' else 120):
                        raise ValueError
                    val = raw
            except (ValueError, OverflowError):
                raise ValueError(f'Проверь значение в поле «{label}».') from None
            values[key] = val
        return values

    def insert(table, values):
        values = dict(values, user_id=g.user['id'])
        db().execute(f'INSERT INTO {table} ({",".join(values)}) VALUES ({",".join("?" for _ in values)})',
                     tuple(values.values()))

    @app.get('/')
    def index():
        conn = db()
        current = date.today().isoformat()
        settings = conn.execute('SELECT * FROM user_settings WHERE user_id=?', (g.user['id'],)).fetchone()
        meals = conn.execute('SELECT COALESCE(SUM(kcal),0),COALESCE(SUM(protein),0) FROM food WHERE user_id=? AND day=?',(g.user['id'],current)).fetchone()
        latest = conn.execute('SELECT weight,day FROM body WHERE user_id=? AND weight IS NOT NULL ORDER BY day DESC,id DESC LIMIT 1',(g.user['id'],)).fetchone()
        planned = conn.execute("SELECT * FROM plan WHERE user_id=? AND weekday=? ORDER BY kind,id",(g.user['id'],date.today().isoweekday())).fetchall()
        return render_template('home.html', settings=settings, meals=meals, latest=latest, planned=planned)

    @app.post('/plan/import')
    def import_plan():
        data = json.loads(Path(app.root_path, 'starter_plan.json').read_text(encoding='utf-8'))
        conn = db()
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            existing = {
                tuple(row) for row in conn.execute(
                    'SELECT weekday, kind, name FROM plan WHERE user_id=?', (g.user['id'],)
                )
            }
            added = 0
            for row in data:
                if (row['weekday'], row['kind'], row['name']) not in existing:
                    insert('plan', row)
                    added += 1
        flash(f'Добавлено строк плана: {added}.' if added else 'Стартовый план уже добавлен.')
        return redirect(url_for('journal', table='plan'))

    @app.get('/menu')
    def menu():
        week = json.loads(Path(app.root_path, 'starter_menu.json').read_text(encoding='utf-8'))
        return render_template('menu.html', week=week)

    @app.get('/menu/<int:day_index>/<int:meal_index>/log')
    def log_meal(day_index, meal_index):
        week = json.loads(Path(app.root_path, 'starter_menu.json').read_text(encoding='utf-8'))
        if day_index >= len(week) or meal_index >= len(week[day_index]['meals']):
            abort(404)
        values = dict(week[day_index]['meals'][meal_index], day=date.today().isoformat())
        title, fields = TABS['food']
        return render_template('form.html', title=title, fields=fields, values=values,
                               error=None, table='food', action=url_for('edit', table='food'))

    @app.get('/journal/<table>')
    def journal(table):
        if table not in TABS:
            abort(404)
        title, fields = TABS[table]
        order = "weekday, kind, id" if table == 'plan' else "day DESC, id DESC"
        selected = request.args.get('day','') if table != 'plan' else ''
        where = 'WHERE user_id=? AND day=?' if selected else 'WHERE user_id=?'
        rows = db().execute(f'SELECT * FROM {table} {where} ORDER BY {order}', (g.user['id'],selected) if selected else (g.user['id'],)).fetchall()
        totals = [sum(r[k] or 0 for r in rows) for k in ('kcal','protein')] if table == 'food' and selected else None
        return render_template('journal.html', table=table,title=title,fields=fields,rows=rows,selected=selected,totals=totals)

    @app.route('/journal/<table>/new', methods=['GET','POST'])
    @app.route('/journal/<table>/<int:row_id>/edit', methods=['GET','POST'])
    def edit(table, row_id=None):
        if table not in TABS:
            abort(404)
        title, fields = TABS[table]
        values = {'day': request.args.get('day') or date.today().isoformat(), 'weekday': date.today().isoweekday(), 'kind':'strength'}
        if row_id is not None:
            found = db().execute(f'SELECT * FROM {table} WHERE id=? AND user_id=?',(row_id,g.user['id'])).fetchone()
            if found is None:
                abort(404)
            values = dict(found)
        error = None
        if request.method == 'POST':
            try:
                values = validate(fields)
                if table == 'plan':
                    if values['kind'] == 'strength':
                        if not values['sets'] or not values['reps']:
                            raise ValueError('Для силового упражнения укажи подходы и повторения.')
                        values['minutes'] = None
                    else:
                        if not values['minutes']:
                            raise ValueError('Для кардио укажи минуты.')
                        values.update(sets=None,reps=None,load=None)
                if table == 'body' and not any(values[k] for k in ('weight','waist','hip','chest')):
                    raise ValueError('Укажи хотя бы один замер.')
                with db():
                    if row_id is None:
                        insert(table, values)
                    else:
                        cols = ','.join(f'{key}=?' for key in values)
                        db().execute(f'UPDATE {table} SET {cols} WHERE id=? AND user_id=?',(*values.values(),row_id,g.user['id']))
                flash('Сохранено.')
                return redirect(url_for('journal',table=table, day=values['day']) if 'day' in values else url_for('journal',table=table))
            except ValueError as exc:
                error, values = str(exc), request.form
        return render_template('form.html', title=title, fields=fields, values=values, error=error, table=table), 422 if error else 200

    @app.post('/journal/<table>/<int:row_id>/delete')
    def delete(table,row_id):
        if table not in TABS:
            abort(404)
        with db():
            if not db().execute(f'DELETE FROM {table} WHERE id=? AND user_id=?',(row_id,g.user['id'])).rowcount:
                abort(404)
        flash('Запись удалена.')
        return redirect(url_for('journal',table=table))

    @app.get('/plan/<int:row_id>/log')
    def log(row_id):
        row = db().execute('SELECT * FROM plan WHERE id=? AND user_id=?',(row_id,g.user['id'])).fetchone()
        if row is None:
            abort(404)
        table = row['kind']
        title, fields = TABS[table]
        values = dict(row)
        values['day'] = date.today().isoformat()
        return render_template('form.html', title=title, fields=fields, values=values, error=None, table=table,
                               action=url_for('edit',table=table))

    @app.route('/settings', methods=['GET','POST'])
    def settings():
        values = dict(db().execute('SELECT * FROM user_settings WHERE user_id=?',(g.user['id'],)).fetchone())
        error = None
        if request.method == 'POST':
            try:
                values = validate(SETTINGS)
                with db():
                    db().execute('UPDATE user_settings SET name=:name,kcal=:kcal,protein=:protein WHERE user_id=:user_id',dict(values,user_id=g.user['id']))
                flash('Цели сохранены.')
                return redirect(url_for('settings'))
            except ValueError as exc:
                error, values = str(exc), request.form
        return render_template('form.html',title='Настройки', fields=SETTINGS,values=values,error=error), 422 if error else 200

    @app.get('/progress')
    def progress():
        conn = db()
        exercises = [r[0] for r in conn.execute('SELECT DISTINCT name FROM strength WHERE user_id=? ORDER BY name',(g.user['id'],))]
        exercise = request.args.get('exercise',exercises[0] if exercises else '')
        series = []
        for key, label in [('weight','Вес тела, кг'),('waist','Талия, см'),('hip','Бёдра, см'),('chest','Грудь, см')]:
            series.append((label, [tuple(r) for r in conn.execute(f'SELECT day,{key} FROM body WHERE user_id=? AND {key} IS NOT NULL ORDER BY day,id',(g.user['id'],))]))
        for label, sql, params in [
            ('Максимальный рабочий вес за день, кг','SELECT day, MAX(load) FROM strength WHERE user_id=? AND name=? AND load IS NOT NULL GROUP BY day ORDER BY day',(g.user['id'],exercise)),
            ('Повторения за день: подходы × повторы','SELECT day,SUM(sets*reps) FROM strength WHERE user_id=? AND name=? GROUP BY day ORDER BY day',(g.user['id'],exercise)),
            ('Кардио за день, мин','SELECT day,SUM(minutes) FROM cardio WHERE user_id=? GROUP BY day ORDER BY day',(g.user['id'],)),
            ('Энергия за день, ккал','SELECT day,SUM(kcal) FROM food WHERE user_id=? GROUP BY day ORDER BY day',(g.user['id'],)),
            ('Белок за день, г','SELECT day,SUM(protein) FROM food WHERE user_id=? GROUP BY day ORDER BY day',(g.user['id'],)),
        ]:
            series.append((label,[tuple(r) for r in conn.execute(sql,params)]))
        return render_template('progress.html', series=series, exercises=exercises, exercise=exercise)

    @app.cli.command('seed-plan')
    @click.option('--username', required=True)
    def seed_plan(username):
        g.user = db().execute('SELECT id FROM users WHERE username=?',(username.casefold(),)).fetchone()
        if g.user is None:
            raise click.ClickException('Пользователь не найден.')
        import json
        data = json.loads(Path(app.root_path,'starter_plan.json').read_text(encoding='utf-8'))
        with db():
            if db().execute('SELECT 1 FROM plan WHERE user_id=? LIMIT 1',(g.user['id'],)).fetchone():
                print('План уже содержит записи. Импорт отменён.')
                return
            for row in data:
                insert('plan',row)
        print(f'Добавлено строк: {len(data)}.')

    return app
