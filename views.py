# -*- coding: utf-8 -*-
import hashlib
import logging
import os
from uuid import uuid4
import msgpack
from flask import request, render_template, session, flash, redirect, url_for, current_app, json
from pymongo.errors import DuplicateKeyError
import sh
import yaml


log = logging.getLogger()

def logged_in(func):
    def wrapper(*args, **kwargs):
        username = session.get('logged_in')
        if not username:
            return redirect(url_for('login'))

        user = current_app.mongo.db.users.find_one({'_id': session['logged_in']})
        if user is None:
            session.pop('logged_in', None)
            return redirect(url_for('login'))

        return func(user, *args, **kwargs)

    return wrapper


def token_required(func):
    def wrapper(*args, **kwargs):
        token = request.values.get('token')
        if not token:
            return 'Token is required', 403
        user = current_app.mongo.db.users.find_one({'token': token})
        if user is None:
            return 'Valid token is required', 403
        return func(*args, token=token, **kwargs)

    return wrapper


def uniform(func):
    def wrapper(*args, **kwargs):
        rv = func(*args, **kwargs)
        if isinstance(rv, basestring):
            code = 200
        else:
            rv, code = rv

        if request.referrer:
            if 200 <= code < 300:
                flash(rv, 'alert-success')
            elif 400 <= code < 600:
                flash(rv, 'alert-error')
            return redirect(request.referrer)

        return rv, code

    return wrapper


def home():
    if 'logged_in' in session:
        return redirect(url_for('dashboard'))
    return render_template('home.html')


def create_user(username, password, admin=False):
    return current_app.mongo.db.users.insert(
        {'_id': username, 'password': hashlib.sha1(password).hexdigest(), 'admin': admin, 'token': str(uuid4())},
        safe=True)


def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if not username or not password:
            return render_template('register.html', error="Username/password cannot be empty")

        try:
            create_user(username, password)
        except DuplicateKeyError as e:
            return render_template('register.html', error="Username is not available")

        session['logged_in'] = username
        flash('You are registered')

        return redirect(url_for('dashboard'))
    return render_template('register.html')


def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = current_app.mongo.db.users.find_one({'_id': username})
        if user is None:
            return render_template('login.html', error='Invalid username')

        if user['password'] != hashlib.sha1(password).hexdigest():
            return render_template('login.html', error='Invalid password')

        session['logged_in'] = user["_id"]
        flash('You were logged in')
        return redirect(url_for('dashboard'))

    return render_template('login.html')


def logout():
    session.pop('logged_in', None)
    flash('You were logged out')
    return redirect(url_for('login'))


def read_and_unpack(key):
    return msgpack.unpackb(current_app.elliptics.read(key))


def key(prefix, postfix):
    if type(postfix) in set([tuple, list, set]):
        return type(postfix)(["%s\0%s" % (prefix, p) for p in postfix])

    return "%s\0%s" % (prefix, postfix)


def remove_prefix(prefix, key):
    prefix = "%s\0" % prefix
    if isinstance(key, dict):
        return dict((k.replace(prefix, ''), v) for k, v in key.items())

    return key.replace(prefix, '')


@logged_in
def dashboard(user):
    if not user['admin']:
        return render_template('dashboard.html', user=user)

    manifests = {}
    runlists = []
    tokens = set()
    try:
        manifests = current_app.elliptics.bulk_read(key("manifests", read_and_unpack(key('system', 'list:manifests'))))
        manifests = remove_prefix("manifests", manifests)
        for k, manifest in manifests.items():
            manifest_unpacked = msgpack.unpackb(manifest)
            manifests[k] = manifest_unpacked
            token = manifest_unpacked.get('developer')
            if token:
                tokens.add(token)
        runlists = read_and_unpack(key('system', 'list:runlists'))
    except RuntimeError:
        pass

    if tokens:
        users = current_app.mongo.db.users.find({'token': {'$in': list(tokens)}})
        tokens = dict((u['token'], u['_id']) for u in users)

    return render_template('dashboard.html', user=user, manifests=manifests, runlists=runlists, tokens=tokens)


@token_required
def create_profile(name, token=None):
    body = request.json
    if body:
        id = '%s_%s' % (token, name)
        body['_id'] = id
        current_app.mongo.db.profiles.update({'_id': id}, body, upsert=True)

    return ''


def read():
    return current_app.elliptics.read_data(key("system", "list:manifests"))


def exists(prefix, postfix):
    return current_app.elliptics.read_data(key(prefix, postfix))


def upload_app(app, info, ref, token):
    info['uuid'] = ("%s_%s" % (info['name'], ref)).strip()
    info['developer'] = token

    e = current_app.elliptics

    app_key = key("apps", info['uuid'])
    current_app.logger.info("Writing app to `%s`" % app_key)
    e.write(app_key, app.read())

    manifest_key = key("manifests", info['uuid'])
    current_app.logger.info("Writing manifest to `%s`" % manifest_key)
    e.write(manifest_key, msgpack.packb(info))

    manifests_key = key("system", "list:manifests")
    manifests = set(msgpack.unpackb(e.read(manifests_key)))
    manifests.add(info['uuid'])
    current_app.logger.info("Adding manifest to list of manifests `%s`" % manifest_key)
    e.write(manifests_key, msgpack.packb(list(manifests)))


@uniform
@token_required
def upload_repo(token):
    url = request.form.get('url')
    type_ = request.form.get('type')
    ref = request.form.get('ref')

    if not url or not type_:
        return 'Empty type or url', 400
    if type_ not in ['git', 'cvs', 'hg']:
        return 'Invalid cvs type', 400

    clone_path = "/tmp/%s" % os.path.basename(url)
    if os.path.exists(clone_path):
        sh.rm("-rf", clone_path)

    if type_ == 'git':
        ref = ref or "HEAD"
        sh.git("clone", url, clone_path)

        try:
            ref = sh.git("rev-parse", ref, _cwd=clone_path).strip()
        except sh.ErrorReturnCode as e:
            return 'Invalid reference. %s' % e, 400

        if not os.path.exists(clone_path + "/info.yaml"):
            return 'info.yaml is required', 400

        package_info = yaml.load(file(clone_path + '/info.yaml'))

        try:
            sh.gzip(
                sh.git("archive", ref, format="tar", prefix=os.path.basename(url) + "/", _cwd=clone_path),
                "-f", _out=clone_path + "/app.tar.gz")
        except sh.ErrorReturnCode as e:
            return 'Unable to pack application. %s' % e, 503

        try:
            with open(clone_path + "/app.tar.gz") as app:
                upload_app(app, package_info, ref, token)
        except RuntimeError:
            return "App storage failure", 500

    return "Application was successfully uploaded"


@uniform
@token_required
def upload(ref, token):
    app = request.files.get('app')
    info = request.form.get('info')

    if app is None or info is None:
        return 'Invalid params', 400

    try:
        info = json.loads(info)
    except Exception as e:
        log.exception('Bad encoded json in info parameter')
        return 'Bad encoded json', 400

    package_type = info.get('type')
    if package_type not in ['python']:
        return '%s type is not supported' % package_type, 400

    app_name = info.get('name')
    if app_name is None:
        return 'App name is required in info file', 400

    try:
        upload_app(app, info, ref, token)
    except RuntimeError:
        return "App storage failure", 500

    return 'ok'
