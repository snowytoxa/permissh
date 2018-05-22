#!/usr/bin/env python3

import logging
import os
import traceback
import sys
import gevent
import gevent.subprocess
import gevent.server
from gevent import monkey

if 'threading' in sys.modules:
    del sys.modules['threading']
monkey.patch_all()

import paramiko

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

class Server(paramiko.ServerInterface):
    def __init__(self, transport):
        paramiko.ServerInterface.__init__(self)
        self.host_key = paramiko.RSAKey(filename=os.path.sep.join([
            os.path.dirname(__file__), '../containerfs/ssh_host_rsa_key']))

        transport.load_server_moduli()
        transport.add_server_key(self.host_key)
        self.transport = transport

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED

    def check_auth_password(self, username, password):
        logging.info(f'Got authentication request for {username} with pw: {password}')
        return paramiko.AUTH_SUCCESSFUL

    def check_auth_publickey(self, username, key):
        logging.info(
            f'Got authentication request for {username} with key: {key.get_fingerprint().hex()}')
        return paramiko.AUTH_SUCCESSFUL

    def get_allowed_auths(self, username):
        return 'password,publickey'

    def check_channel_exec_request(self, channel, cmd):
        logger.info("Got exec request on channel %s for cmd %s" %
                     (channel, cmd,))
        _env = os.environ
        if hasattr(channel, 'environment'):
            _env.update(channel.environment)
        process = gevent.subprocess.Popen(cmd, stdout=gevent.subprocess.PIPE,
                                          stdin=gevent.subprocess.PIPE,
                                          stderr=gevent.subprocess.PIPE,
                                          shell=True, env=_env,
                                          bufsize=0)
        gevent.spawn(self._read_response, channel.send,
                     process, process.stdout.fileio, channel)
        gevent.spawn(self._read_response, channel.send_stderr,
                     process, process.stderr.fileio, channel)
        gevent.spawn(self._read_returncode, channel, process)
        gevent.spawn(self._write_input, channel, process)

        gevent.sleep(0)
        return True

    def _read_response(self, channel_func, process, stdpipe, channel):
        gevent.sleep(0)
        while process.returncode is None and not channel.exit_status_ready():
            gevent.sleep(0)
            data = stdpipe.read(1024)
            if data:
                channel_func(data)

    def _read_returncode(self, channel, process):
        gevent.sleep(0)
        while process.returncode is None and not channel.exit_status_ready():
            gevent.sleep(.1)
        logger.info("Command finished with return code %s",
                    process.returncode)
        if process.returncode:
            channel.send_exit_status(process.returncode)
        gevent.sleep(.1)
        channel.close()
        gevent.sleep(0)

    def _write_input(self, channel, process):
        gevent.sleep(0)
        while process.returncode is None and not channel.exit_status_ready():
            gevent.sleep(0)
            if channel.recv_ready():
                data = channel.recv(1024)
                logger.debug(f'stdin::: {len(data)}')
                logger.debug(f'received data: {data}')
                process.stdin.write(data)
                process.stdin.flush()

    def check_channel_env_request(self, channel, name, value):
        if not hasattr(channel, 'environment'):
            channel.environment = {}
        channel.environment.update({
            name.decode('utf-8'): value.decode('utf-8')})
        return True

def _handle_ssh_connection(transport):
    server = Server(transport)
    try:
        transport.start_server(server=server)
    except paramiko.SSHException as e:
        logger.exception('SSH negotiation failed')
        return
    except Exception:
        logger.exception("Error occured starting server")
        return
    gevent.sleep(2)
    channel = transport.accept(20)
    gevent.sleep(0)
    if not channel:
        logger.error("Could not establish channel")
        return
    while transport.is_active():
        logger.debug("Transport active, waiting..")
        gevent.sleep(1)
    while not channel.send_ready():
        gevent.sleep(.2)
    channel.close()
    gevent.sleep(0)

def handle_ssh_connection(conn, addr):
    logger.info('Got connection..')
    try:
        transport = paramiko.Transport(conn)
        return _handle_ssh_connection(transport)
    except Exception as e:
        logger.error('*** Caught exception: %s: %s' %
                     (str(e.__class__), str(e),))
        traceback.print_exc()
        try:
            transport.close()
        except:
            pass


if __name__ == "__main__":
    _server = gevent.server.StreamServer(
        ('0.0.0.0', 2200), handle_ssh_connection)
    _server.serve_forever()
