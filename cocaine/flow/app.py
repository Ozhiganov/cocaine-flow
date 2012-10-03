#!/usr/bin/env python
import logging
from flask import Flask
from flask.ext.pymongo import PyMongo
from pymongo.errors import DuplicateKeyError
from common import key
from storages import create_storage
import views
import api_settings as settings

try:
    from collections import Mapping as MappingType
except ImportError:
    import UserDict
    MappingType = (UserDict.UserDict, UserDict.DictMixin, dict)


def test_mapping(value):
    return isinstance(value, MappingType)


def create_app():
    app = Flask(__name__)
    app.config.from_pyfile('api_settings.py')

    app.add_url_rule('/', view_func=views.home)
    app.add_url_rule('/register', view_func=views.register, methods=['GET', 'POST'])
    app.add_url_rule('/login', 'login', view_func=views.login, methods=['GET', 'POST'])
    app.add_url_rule('/logout', view_func=views.logout)
    app.add_url_rule('/dashboard', 'dashboard', view_func=views.dashboard, methods=['GET', 'POST'])
    app.add_url_rule('/stats', 'stats', view_func=views.stats, methods=['GET'])
    app.add_url_rule('/profiles/<string:name>', view_func=views.create_profile, methods=['POST'])
    app.add_url_rule('/exists/<string:prefix>/<string:postfix>', view_func=views.exists)
    app.add_url_rule('/upload', endpoint="upload", view_func=views.upload, methods=['POST'])
    app.add_url_rule('/deploy/<string:runlist>/<string:uuid>/<string:profile>', endpoint="deploy",
                     view_func=views.deploy, methods=['POST'])
    app.add_url_rule('/hosts/', view_func=views.get_hosts, methods=['GET'])
    app.add_url_rule('/hosts/<string:host>', view_func=views.create_host, methods=['POST', 'PUT'])
    app.add_url_rule('/hosts/<string:host>', view_func=views.delete_host, methods=['DELETE'])
    app.add_url_rule('/app/<string:app_name>', view_func=views.delete_app, methods=['DELETE'])
    app.add_url_rule('/app/start/<string:uuid>/<string:profile>', view_func=views.start_app, methods=['POST'])
    app.add_url_rule('/app/stop/<string:uuid>', view_func=views.stop_app, methods=['POST'])
    app.add_url_rule('/maintenance', view_func=views.maintenance, methods=['POST'])
    app.add_url_rule('/token', view_func=views.get_token, methods=['POST'])


    app.error_handler_spec[None][500] = views.error_handler

    app.mongo = PyMongo(app)
    app.storage = create_storage()

    logging.basicConfig(level=logging.DEBUG)

    if 'mapping' not in app.jinja_env.tests:
        app.jinja_env.tests['mapping'] = test_mapping

    return app


def install(username, password):
    app = create_app()
    with app.test_request_context():
        try:
            views.create_user(username, password, admin=True)
        except DuplicateKeyError:
            print 'Username is busy'
            return False

        try:
            app.storage.read(key('system', 'list:runlists'))
        except RuntimeError:
            try:
                app.storage.write(key('system', 'list:runlists'), ['default'])
            except RuntimeError:
                print 'Storage failure'
                return False
    return True


if __name__ == '__main__':
    import api_settings as settings

    app = create_app()
    app.run(debug=True, host=settings.HOSTNAME, port=settings.PORT)
