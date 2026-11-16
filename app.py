import os, sqlite3
from secrets import token_hex
from datetime import date
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify

BASE=os.path.dirname(os.path.abspath(__file__))
DB=os.path.join(BASE,'fitjournal.sqlite3')
app=Flask(__name__)
app.secret_key=os.environ.get('SECRET_KEY') or token_hex(32)

def conn():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c

def init():
    with conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS exercises(id INTEGER PRIMARY KEY,name TEXT NOT NULL,day INTEGER NOT NULL,sets INTEGER NOT NULL DEFAULT 3,reps TEXT NOT NULL DEFAULT '8-12',sort INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY,day INTEGER NOT NULL,done_on TEXT NOT NULL,cardio_kind TEXT,cardio_minutes REAL DEFAULT 0,notes TEXT);
        CREATE TABLE IF NOT EXISTS sets(id INTEGER PRIMARY KEY,session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,exercise_id INTEGER REFERENCES exercises(id) ON DELETE SET NULL,exercise_name TEXT NOT NULL,set_number INTEGER NOT NULL,reps INTEGER NOT NULL,weight REAL NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS measurements(id INTEGER PRIMARY KEY,done_on TEXT NOT NULL,weight REAL,waist REAL,sleep REAL,notes TEXT);
        CREATE TABLE IF NOT EXISTS food(id INTEGER PRIMARY KEY,done_on TEXT NOT NULL,name TEXT NOT NULL,grams REAL,calories REAL NOT NULL DEFAULT 0,protein REAL NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS meals(id INTEGER PRIMARY KEY,day INTEGER NOT NULL,meal TEXT NOT NULL,description TEXT NOT NULL,calories REAL DEFAULT 0,protein REAL DEFAULT 0);
        ''')
        defaults = dict.fromkeys(
            ('weight_goal', 'calorie_goal', 'protein_goal', 'height', 'starting_weight',
             'cardio_upper', 'cardio_legs'),
            '',
        )
        for k,v in defaults.items():c.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',(k,v))
        if not c.execute('SELECT 1 FROM exercises LIMIT 1').fetchone():
            days={
            1:[('Жим в грудном тренажёре',3,'8–12'),('Сведение рук в тренажёре',2,'12–15'),('Жим гантелей на наклонной скамье',2,'8–12'),('Разгибание рук на блоке',2,'10–15')],
            2:[('Тяга верхнего блока',3,'8–12'),('Горизонтальная тяга',3,'8–12'),('Обратная бабочка',2,'12–15'),('Сгибание рук с гантелями',2,'10–12')],
            3:[('Жим ногами',3,'10–15'),('Сгибание ног в тренажёре',3,'10–15'),('Разгибание ног в тренажёре',2,'12–15'),('Подъём на носки',3,'12–20'),('Планка',2,'20–40 сек')],
            4:[('Жим в грудном тренажёре (лёгкий)',2,'10–12'),('Жим плечами в тренажёре',2,'8–12'),('Подъём гантелей в стороны',3,'12–15'),('Сведение рук в тренажёре',2,'12–15')],
            5:[('Тяга верхнего блока нейтральным хватом',3,'8–12'),('Горизонтальная тяга узким хватом',2,'10–12'),('Обратная бабочка',2,'12–15'),('Сгибание рук на блоке',2,'10–15')],
            6:[('Жим ногами (лёгкий)',2,'12–15'),('Сгибание ног',2,'12–15'),('Ягодичный мост',2,'10–15'),('Подъём на носки',2,'15–20'),('Скручивания на блоке',2,'10–15')]}
            for day,items in days.items():
                for i,(n,s,r) in enumerate(items):c.execute('INSERT INTO exercises(name,day,sets,reps,sort) VALUES(?,?,?,?,?)',(n,day,s,r,i))

DAYS=['Понедельник · грудь и трицепс','Вторник · спина и бицепс','Среда · ноги и корпус','Четверг · грудь и плечи','Пятница · спина и бицепс','Суббота · ноги (лёгкая)','Воскресенье · отдых']
@app.context_processor
def globals_():return dict(days=DAYS,today=date.today().isoformat())
@app.route('/')
def home():
    with conn() as c:
        measurements=c.execute('SELECT * FROM measurements ORDER BY done_on DESC,id DESC LIMIT 1').fetchone()
        sessions=c.execute('SELECT COUNT(*) n FROM sessions').fetchone()['n']
        food=c.execute('SELECT COALESCE(SUM(calories),0) cal,COALESCE(SUM(protein),0) protein FROM food WHERE done_on=?',(date.today().isoformat(),)).fetchone()
        settings=dict((r['key'],r['value']) for r in c.execute('SELECT * FROM settings'))
    return render_template('home.html',measurements=measurements,sessions=sessions,food=food,settings=settings)
@app.route('/plan',methods=['GET','POST'])
def plan():
    with conn() as c:
        if request.method=='POST':
            action=request.form.get('action')
            if action=='add':c.execute('INSERT INTO exercises(name,day,sets,reps,sort) VALUES(?,?,?,?,99)',(request.form['name'],int(request.form['day']),int(request.form['sets']),request.form['reps']))
            if action=='edit':c.execute('UPDATE exercises SET name=?,day=?,sets=?,reps=? WHERE id=?',(request.form['name'],int(request.form['day']),int(request.form['sets']),request.form['reps'],int(request.form['id'])))
            if action=='delete':c.execute('DELETE FROM exercises WHERE id=?',(int(request.form['id']),))
            return redirect(url_for('plan'))
        ex=c.execute('SELECT * FROM exercises ORDER BY day,sort,id').fetchall()
    return render_template('plan.html',ex=ex)
@app.route('/workout',methods=['GET','POST'])
def workout():
    with conn() as c:
        if request.method=='POST':
            day=int(request.form['day']); d=request.form['done_on']; cardio=float(request.form.get('cardio_minutes') or 0)
            cur=c.execute('INSERT INTO sessions(day,done_on,cardio_kind,cardio_minutes,notes) VALUES(?,?,?,?,?)',(day,d,request.form.get('cardio_kind'),cardio,request.form.get('notes')))
            sid=cur.lastrowid
            ids=request.form.getlist('exercise_id'); nums=request.form.getlist('set_number'); reps=request.form.getlist('reps'); weights=request.form.getlist('weight')
            for eid,n,r,w in zip(ids,nums,reps,weights):
                if r.strip():
                    row=c.execute('SELECT name FROM exercises WHERE id=?',(eid,)).fetchone()
                    if row:c.execute('INSERT INTO sets(session_id,exercise_id,exercise_name,set_number,reps,weight) VALUES(?,?,?,?,?,?)',(sid,int(eid),row['name'],int(n),int(r),float(w or 0)))
            flash('Тренировка сохранена');return redirect(url_for('history'))
        ex=c.execute('SELECT * FROM exercises ORDER BY day,sort,id').fetchall()
    return render_template('workout.html',ex=ex)
@app.route('/history',methods=['GET','POST'])
def history():
    with conn() as c:
        if request.method=='POST':
            c.execute('DELETE FROM sessions WHERE id=?',(int(request.form['id']),));return redirect(url_for('history'))
        sessions=c.execute('SELECT * FROM sessions ORDER BY done_on DESC,id DESC').fetchall()
        sets=c.execute('SELECT * FROM sets ORDER BY session_id DESC,exercise_name,set_number').fetchall()
    return render_template('history.html',sessions=sessions,sets=sets)
@app.route('/measurements',methods=['GET','POST'])
def measurements():
    with conn() as c:
        if request.method=='POST':
            if request.form.get('action')=='delete':c.execute('DELETE FROM measurements WHERE id=?',(int(request.form['id']),))
            else:c.execute('INSERT INTO measurements(done_on,weight,waist,sleep,notes) VALUES(?,?,?,?,?)',(request.form['done_on'],num(request.form.get('weight')),num(request.form.get('waist')),num(request.form.get('sleep')),request.form.get('notes')))
            return redirect(url_for('measurements'))
        rows=c.execute('SELECT * FROM measurements ORDER BY done_on DESC,id DESC').fetchall()
    return render_template('measurements.html',rows=rows)
def num(v):return float(v) if v and v.strip() else None
@app.route('/nutrition',methods=['GET','POST'])
def nutrition():
    with conn() as c:
        if request.method=='POST':
            if request.form.get('action')=='delete':c.execute('DELETE FROM food WHERE id=?',(int(request.form['id']),))
            else:c.execute('INSERT INTO food(done_on,name,grams,calories,protein) VALUES(?,?,?,?,?)',(request.form['done_on'],request.form['name'],num(request.form.get('grams')),float(request.form.get('calories') or 0),float(request.form.get('protein') or 0)))
            return redirect(url_for('nutrition'))
        rows=c.execute('SELECT * FROM food ORDER BY done_on DESC,id DESC LIMIT 200').fetchall()
    return render_template('nutrition.html',rows=rows)
@app.route('/menu',methods=['GET','POST'])
def menu():
    with conn() as c:
        if request.method=='POST':
            if request.form.get('action')=='delete':c.execute('DELETE FROM meals WHERE id=?',(int(request.form['id']),))
            elif request.form.get('action')=='edit':c.execute('UPDATE meals SET day=?,meal=?,description=?,calories=?,protein=? WHERE id=?',(int(request.form['day']),request.form['meal'],request.form['description'],float(request.form['calories']),float(request.form['protein']),int(request.form['id'])))
            else:c.execute('INSERT INTO meals(day,meal,description,calories,protein) VALUES(?,?,?,?,?)',(int(request.form['day']),request.form['meal'],request.form['description'],float(request.form['calories']),float(request.form['protein'])))
            return redirect(url_for('menu'))
        rows=c.execute('SELECT * FROM meals ORDER BY day,id').fetchall()
    return render_template('menu.html',rows=rows)
@app.route('/settings',methods=['GET','POST'])
def settings():
    with conn() as c:
        if request.method=='POST':
            for k in ['weight_goal','calorie_goal','protein_goal','height','starting_weight','cardio_upper','cardio_legs']:
                if k in request.form:c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(k,request.form[k]))
            flash('Настройки сохранены');return redirect(url_for('settings'))
        rows=dict((r['key'],r['value']) for r in c.execute('SELECT * FROM settings'))
    return render_template('settings.html',settings=rows)
@app.route('/charts')
def charts():return render_template('charts.html')
@app.route('/api/charts')
def chart_data():
    with conn() as c:
        m=[dict(r) for r in c.execute('SELECT * FROM measurements ORDER BY done_on,id')]
        f=[dict(r) for r in c.execute('SELECT done_on,SUM(calories) calories,SUM(protein) protein FROM food GROUP BY done_on ORDER BY done_on')]
        strength=[dict(r) for r in c.execute('SELECT s.done_on,t.exercise_name,MAX(t.weight) weight,MAX(t.weight*t.reps) volume FROM sets t JOIN sessions s ON s.id=t.session_id GROUP BY s.done_on,t.exercise_name ORDER BY s.done_on')]
        cardio=[dict(r) for r in c.execute('SELECT done_on,SUM(cardio_minutes) minutes FROM sessions GROUP BY done_on ORDER BY done_on')]
    return jsonify(measurements=m,food=f,strength=strength,cardio=cardio)
init()
if __name__=='__main__':app.run(debug=os.environ.get('FLASK_DEBUG')=='1',host='127.0.0.1',port=int(os.environ.get('PORT','5000')))
