"""WEB_CONCURRENCY: several serve processes share one socket and one set of rate-limit counters."""
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import helpers  # noqa: E402
from helpers import API, P, base_env, make_cfg, reset_db  # noqa: E402

CLI = os.path.join(os.path.dirname(__file__), "..", "server", "cli.py")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Workers(unittest.TestCase):
    def setUp(self):
        reset_db()

    def test_defaults_and_bounds(self):
        self.assertEqual((make_cfg().web_concurrency, make_cfg().worker_fd), (1, 0))          # the test environment pins 1
        with helpers.environment(dict(base_env(), WEB_CONCURRENCY=None)):
            self.assertEqual(helpers.config.Config("serve").web_concurrency, 4)               # the production default
        self.assertEqual(make_cfg(WEB_CONCURRENCY="8").web_concurrency, 8)
        with self.assertRaises(helpers.config.ConfigError):
            make_cfg(WEB_CONCURRENCY="99")

    def test_four_workers_share_the_socket_and_the_limits_and_stop_together(self):
        port = free_port()
        env = {k: v for k, v in os.environ.items() if not k.startswith("TIMUS_")}
        env.update({(k if k in ("TIMUS_ENV", "PORT", "HOST") else P + k): v for k, v in dict(base_env(), PORT=str(port), WEB_CONCURRENCY="4",
                                                                                          WRITE_RATE_PER_HOUR="30").items()})
        log = tempfile.TemporaryFile("w+")                                                    # a file, not a pipe: a full pipe would block the logging workers
        self.addCleanup(log.close)
        proc = subprocess.Popen([sys.executable, CLI, "serve"], env=env, stdout=log, stderr=subprocess.STDOUT, text=True)
        try:
            for _ in range(100):
                try:
                    c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                    c.request("GET", API + "/health")
                    if c.getresponse().status == 200:
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                self.fail("the server did not start")
            kids = subprocess.run(["pgrep", "-P", str(proc.pid)], capture_output=True, text=True).stdout.split()
            self.assertEqual(len(kids), 3)                                                    # the leader plus three workers
            codes = []
            for _ in range(150):                                                              # new connections: spread over the workers
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                c.request("POST", API + "/map/yandex-failed", json.dumps({"keyId": 1}), {"Content-Type": "application/json", "X-Forwarded-For": "198.51.100.9"})
                r = c.getresponse()
                r.read()
                codes.append(r.status)
                c.close()
            self.assertEqual((codes.count(200), codes.count(429)), (30, 120))                 # one budget for all workers, not 30 each
        finally:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                subprocess.run(["pkill", "-f", "server/cli.py serve"])
                log.seek(0)
                self.fail("the container did not stop on SIGTERM:\n" + log.read()[-3000:])
            log.seek(0)
            out = log.read()
        self.assertEqual(proc.returncode, 0, out)
        self.assertEqual(out.count("] app - stopped"), 4, out)
        time.sleep(0.3)
        alive = [p for p in kids if subprocess.run(["kill", "-0", p], capture_output=True).returncode == 0]
        self.assertEqual(alive, [])                                                           # the workers stopped with the leader


if __name__ == "__main__":
    unittest.main()
