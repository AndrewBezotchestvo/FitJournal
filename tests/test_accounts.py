import sqlite3
import tempfile
import unittest
from pathlib import Path

from fitjournal import create_app

PASSWORD = 'local-test-password-42'


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = str(Path(self.temp.name) / 'journal.sqlite3')
        self.app = create_app({'TESTING': True, 'DATABASE': self.database})
        self.client = self.app.test_client()

    def token(self, client, path='/login'):
        client.get(path)
        with client.session_transaction() as cookie:
            return cookie['csrf']

    def post(self, client, path, **fields):
        with client.session_transaction() as cookie:
            token = cookie.get('csrf')
        if not token:
            token = self.token(client)
        return client.post(path, data={'csrf': token, **fields})

    def register(self, name='alice', client=None):
        client = client or self.client
        self.token(client, '/register')
        response = self.post(client, '/register', username=name, password=PASSWORD, confirm=PASSWORD)
        self.assertEqual(response.status_code, 302)
        client.get('/')
        return client

    def test_register_password_hash_login_logout_restart(self):
        self.register()
        with sqlite3.connect(self.database) as conn:
            name, hashed = conn.execute('SELECT username,password_hash FROM users').fetchone()
        self.assertEqual(name, 'alice')
        self.assertNotIn(PASSWORD, hashed)
        self.assertTrue(hashed.startswith('pbkdf2:'))
        self.assertEqual(self.post(self.client, '/logout').status_code, 302)
        self.assertEqual(self.client.get('/settings').status_code, 302)
        self.token(self.client)
        self.assertEqual(self.post(self.client, '/login', username='alice', password='wrong').status_code, 422)
        self.assertEqual(self.post(self.client, '/login', username='ALICE', password=PASSWORD).status_code, 302)
        other_app = create_app({'TESTING': True, 'DATABASE': self.database})
        fresh = other_app.test_client()
        self.token(fresh)
        self.assertEqual(self.post(fresh, '/login', username='alice', password=PASSWORD).status_code, 302)

    def test_duplicate_username_validation_and_csrf(self):
        self.assertEqual(self.client.post('/register',data={'username':'alice'}).status_code,400)
        self.token(self.client)
        for fields in [
            dict(username='a',password=PASSWORD,confirm=PASSWORD),
            dict(username='alice',password='short',confirm='short'),
            dict(username='alice',password=PASSWORD,confirm='different'),
        ]:
            self.assertEqual(self.post(self.client,'/register',**fields).status_code,422)
        self.register()
        other = self.app.test_client()
        self.token(other, '/register')
        self.assertEqual(self.post(other,'/register',username='ALICE',password=PASSWORD,confirm=PASSWORD).status_code,422)

    def test_private_records_and_graphs(self):
        alice = self.register()
        entries = {
            'plan':dict(weekday=1,kind='strength',name='PrivatePlan',sets=2,reps=10),
            'strength':dict(day='2026-11-17',name='PrivateLift',sets=2,reps=10,load=23),
            'cardio':dict(day='2026-11-17',name='PrivateCardio',minutes=19),
            'food':dict(day='2026-11-17',name='PrivateFood',kcal=1234,protein=77),
            'body':dict(day='2026-11-17',weight=139,waist=127,note='PrivateBody')
        }
        for table, data in entries.items():
            self.assertEqual(self.post(alice,f'/journal/{table}/new',**data).status_code,302)
        self.assertEqual(self.post(alice,'/settings',name='PrivateName',kcal=2811,protein=169).status_code,302)
        bob = self.register('bob', self.app.test_client())
        for table, data in entries.items():
            for suffix in ('', '?day=2026-11-17'):
                self.assertNotIn(b'Private',bob.get(f'/journal/{table}{suffix}').data)
            self.assertEqual(bob.get(f'/journal/{table}/1/edit').status_code,404)
            self.assertEqual(self.post(bob,f'/journal/{table}/1/edit',**data).status_code,404)
            self.assertEqual(self.post(bob,f'/journal/{table}/1/delete').status_code,404)
        self.assertEqual(bob.get('/plan/1/log').status_code,404)
        self.assertNotIn(b'Private',bob.get('/').data)
        self.assertNotIn(b'PrivateName',bob.get('/settings').data)
        self.assertNotIn(b'data-points=',bob.get('/progress?exercise=PrivateLift').data)
        self.assertIn(b'data-points=',alice.get('/progress?exercise=PrivateLift').data)
        for table in entries:
            self.assertEqual(self.post(alice,f'/journal/{table}/1/delete').status_code,302)

    def test_forged_owner_is_ignored(self):
        alice = self.register()
        bob = self.register('bob',self.app.test_client())
        self.assertEqual(self.post(bob,'/journal/body/new',day='2026-11-17',weight=125,user_id='1').status_code,302)
        self.assertNotIn(b'125.0',alice.get('/journal/body').data)
        self.assertIn(b'125.0',bob.get('/journal/body').data)

    def test_old_rows_are_preserved_without_assigning_to_first_signup(self):
        with sqlite3.connect(self.database) as conn:
            conn.execute("INSERT INTO body(day,weight) VALUES ('2026-11-16',140)")
        self.register()
        self.assertNotIn(b'140.0',self.client.get('/journal/body').data)
        with sqlite3.connect(self.database) as conn:
            self.assertEqual(conn.execute('SELECT weight,user_id FROM body').fetchone(),(140,None))

    def test_seed_plan_scoped_and_idempotent(self):
        self.register()
        bob = self.register('bob',self.app.test_client())
        runner = self.app.test_cli_runner()
        for _ in range(2):
            result = runner.invoke(args=['seed-plan','--username','alice'])
            self.assertEqual(result.exit_code,0,result.output)
        with sqlite3.connect(self.database) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM plan').fetchone()[0],21)
        self.assertNotIn('Жим ногами',bob.get('/journal/plan').text)
        self.assertIn('Жим ногами',self.client.get('/journal/plan').text)

    def test_remembered_session_survives_restart_and_logout_revokes_it(self):
        self.register()
        self.post(self.client, '/logout')
        login = self.post(self.client, '/login', username='alice', password=PASSWORD, remember='1')
        self.assertIn('Expires=', login.headers['Set-Cookie'])
        cookie = self.client.get_cookie('session').value
        restarted = create_app({'TESTING': True, 'DATABASE': self.database}).test_client()
        restarted.set_cookie('session', cookie)
        self.assertEqual(restarted.get('/').status_code, 200)
        self.post(restarted, '/logout')
        replay = self.app.test_client()
        replay.set_cookie('session', cookie)
        self.assertEqual(replay.get('/').status_code, 302)
        self.token(replay)
        temporary = self.post(replay, '/login', username='alice', password=PASSWORD)
        self.assertNotIn('Expires=', temporary.headers['Set-Cookie'])
        with sqlite3.connect(self.database) as db:
            db.execute('UPDATE device_sessions SET expires_at=0')
        self.assertEqual(replay.get('/').status_code, 302)

    def test_rate_limit(self):
        self.token(self.client)
        for _ in range(30):
            self.assertEqual(self.post(self.client,'/register',username='!',password='',confirm='').status_code,422)
        self.assertEqual(self.post(self.client,'/register',username='!',password='',confirm='').status_code,429)


if __name__ == '__main__':
    unittest.main()
