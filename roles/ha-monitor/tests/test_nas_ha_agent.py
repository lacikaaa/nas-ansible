import importlib.machinery
import importlib.util
import json
import os
import socket
import sqlite3
import tempfile
import threading
import unittest
from collections import namedtuple
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
SCRIPT = os.path.join(HERE, "..", "files", "nas-ha-agent")

_loader = importlib.machinery.SourceFileLoader("nas_ha_agent", SCRIPT)
_spec = importlib.util.spec_from_loader("nas_ha_agent", _loader)
agent = importlib.util.module_from_spec(_spec)
_loader.exec_module(agent)


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


class MqttPacketTests(unittest.TestCase):
    def test_remaining_length_boundaries(self):
        enc = agent.encode_remaining_length
        self.assertEqual(enc(0), b"\x00")
        self.assertEqual(enc(127), b"\x7f")
        self.assertEqual(enc(128), b"\x80\x01")
        self.assertEqual(enc(16383), b"\xff\x7f")
        self.assertEqual(enc(16384), b"\x80\x80\x01")
        self.assertEqual(enc(2097151), b"\xff\xff\x7f")
        self.assertEqual(enc(2097152), b"\x80\x80\x80\x01")
        with self.assertRaises(ValueError):
            enc(268435456)

    def test_connect_bytes(self):
        pkt = agent.build_connect("cid", "user", "pw", keepalive=30)
        expected_body = (b"\x00\x04MQTT\x04\xc2\x00\x1e" + b"\x00\x03cid"
                         + b"\x00\x04user" + b"\x00\x02pw")
        self.assertEqual(pkt, b"\x10" + bytes([len(expected_body)]) + expected_body)

    def test_connect_without_credentials(self):
        pkt = agent.build_connect("c", "", "")
        self.assertEqual(pkt[0], 0x10)
        self.assertEqual(pkt[2 + 6 + 1], 0x02)  # clean session only

    def test_publish_small(self):
        pkt = agent.build_publish("a/b", "hi", retain=True)
        self.assertEqual(pkt, b"\x31\x07\x00\x03a/bhi")
        pkt = agent.build_publish("a/b", b"hi", retain=False)
        self.assertEqual(pkt[0], 0x30)

    def test_publish_large_payloads(self):
        for size, rl_bytes in ((200, 2), (20000, 3)):
            payload = b"x" * size
            pkt = agent.build_publish("t", payload, retain=True)
            body_len = 2 + 1 + size
            self.assertEqual(pkt[1:1 + rl_bytes], agent.encode_remaining_length(body_len))
            self.assertEqual(len(pkt), 1 + rl_bytes + body_len)
            self.assertTrue(pkt.endswith(payload))

    def test_connack(self):
        agent.parse_connack(b"\x20\x02\x00\x00")
        with self.assertRaises(agent.MqttError) as ctx:
            agent.parse_connack(b"\x20\x02\x00\x05")
        self.assertIn("not authorized", str(ctx.exception))
        with self.assertRaises(agent.MqttError):
            agent.parse_connack(b"\x30\x02\x00\x00")

    def _server(self, connack):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        received = []

        def serve():
            conn, _ = srv.accept()
            conn.settimeout(3)
            received.append(conn.recv(4096))
            conn.sendall(connack)
            try:
                while True:
                    data = conn.recv(65536)
                    if not data:
                        break
                    received.append(data)
            except OSError:
                pass
            conn.close()
            srv.close()

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        return srv.getsockname()[1], received, t

    def test_client_roundtrip(self):
        port, received, t = self._server(b"\x20\x02\x00\x00")
        c = agent.MqttClient("127.0.0.1", port, "u", "p", timeout=3)
        c.connect()
        c.publish("nas/state", "{}", retain=False)
        c.disconnect()
        t.join(3)
        blob = b"".join(received)
        self.assertTrue(blob.startswith(b"\x10"))
        self.assertIn(b"\x30\x0d\x00\x09nas/state{}", blob)
        self.assertTrue(blob.endswith(b"\xe0\x00"))

    def test_client_refused(self):
        port, _, t = self._server(b"\x20\x02\x00\x04")
        c = agent.MqttClient("127.0.0.1", port, "u", "bad", timeout=3)
        with self.assertRaises(agent.MqttError):
            c.connect()
        t.join(3)

    def test_client_unreachable(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with self.assertRaises(agent.MqttError):
            agent.MqttClient("127.0.0.1", port, "u", "p", timeout=1).connect()


class ParserTests(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(agent.slug("budlap-1712"), "budlap_1712")
        self.assertEqual(agent.slug("DESKTOP-KVBHJ3P"), "desktop_kvbhj3p")
        self.assertEqual(agent.slug("My.Sick app"), "my_sick_app")

    def test_statvfs(self):
        St = namedtuple("St", "f_blocks f_bfree f_bavail f_frsize")
        gib = 1024 ** 3
        pct, free = agent.parse_statvfs(St(1000, 250, 250, gib // 10))
        self.assertEqual(pct, 75.0)
        self.assertEqual(free, 25.0)
        self.assertEqual(agent.parse_statvfs(St(0, 0, 0, 4096)), (0.0, 0.0))

    def test_btrfs_stats(self):
        self.assertEqual(agent.parse_btrfs_device_stats(fixture("btrfs_device_stats.txt")), 7)
        with self.assertRaises(ValueError):
            agent.parse_btrfs_device_stats("ERROR: not a btrfs\n")

    def test_btrfs_scrub(self):
        when, errors = agent.parse_btrfs_scrub(fixture("btrfs_scrub_status.txt"))
        self.assertTrue(when.startswith("2026-10-13T21:58:32"))
        self.assertRegex(when, r"[+-]\d\d:\d\d$")
        self.assertEqual(errors, 0)
        self.assertEqual(agent.parse_btrfs_scrub(fixture("btrfs_scrub_errors.txt"))[1], 8)
        self.assertEqual(agent.parse_btrfs_scrub(fixture("btrfs_scrub_never.txt")), (None, 0))

    def test_lsblk(self):
        self.assertEqual(agent.parse_lsblk_disks(fixture("lsblk.txt")), ["sda", "sdb", "sdc"])

    def test_smartctl(self):
        out = agent.parse_smartctl(json.loads(fixture("smartctl_sda.json")))
        self.assertEqual(out, {"smart": "OFF", "temperature": 42, "reallocated": 0,
                               "pending": 0, "power_on_hours": 75339})

    def test_smartctl_failed_or_missing_status(self):
        data = json.loads(fixture("smartctl_sda.json"))
        data["smart_status"]["passed"] = False
        self.assertEqual(agent.parse_smartctl(data)["smart"], "ON")
        del data["smart_status"]
        self.assertEqual(agent.parse_smartctl(data)["smart"], "ON")
        with self.assertRaises(ValueError):
            agent.parse_smartctl([])

    def test_smartctl_fallback_to_attributes(self):
        data = json.loads(fixture("smartctl_sda.json"))
        del data["temperature"], data["power_on_time"]
        out = agent.parse_smartctl(data)
        self.assertEqual(out["temperature"], 42)
        self.assertEqual(out["power_on_hours"], 75339)

    def test_is_active(self):
        res = agent.parse_is_active(["a", "b", "c"], "active\ninactive\n")
        self.assertEqual(res, {"a": True, "b": False, "c": False})

    def test_docker(self):
        res = agent.parse_docker_inspect(json.loads(fixture("docker_inspect.json")))
        self.assertEqual(res["plex"]["health"], "healthy")
        self.assertEqual(res["swag"]["health"], "none")
        self.assertEqual(res["my_sick_app"]["name"], "my-sick.app")
        self.assertTrue(agent.container_is_up(res["plex"]))
        self.assertTrue(agent.container_is_up(res["swag"]))
        self.assertFalse(agent.container_is_up(res["my_sick_app"]))
        self.assertFalse(agent.container_is_up(res["stopped_one"]))

    def test_docker_slug_collision_and_odd_slugs(self):
        items = [{"Name": "/a-b", "State": {"Status": "running"}, "Config": {"Image": "x"}},
                 {"Name": "/a.b", "State": {"Status": "exited"}, "Config": {"Image": "y"}},
                 {"Name": "/1st", "State": {"Status": "running"}, "Config": {"Image": "z"}},
                 {"Name": "/items", "State": {"Status": "running"}, "Config": {"Image": "w"}}]
        with mock.patch.object(agent, "log") as lg:
            res = agent.parse_docker_inspect(items)
        self.assertEqual(res["a_b"]["name"], "a-b")
        self.assertEqual(res["a_b"]["image"], "x")
        self.assertEqual(len(res), 3)
        lg.assert_called_once()
        c = agent.binary_sensor("container_1st", "c", json_attributes_template=
                                "{{ value_json['containers_attr']['1st'] | tojson }}")
        self.assertEqual(c["value_template"], "{{ value_json['container_1st'] }}")

    def test_samba(self):
        self.assertEqual(agent.parse_samba_sessions(fixture("smbstatus.json")), 2)
        self.assertEqual(agent.parse_samba_sessions(fixture("smbstatus.txt")), 2)
        self.assertEqual(agent.parse_samba_sessions(fixture("smbstatus_empty.txt")), 0)
        with self.assertRaises(ValueError):
            agent.parse_samba_sessions("garbage")

    def _urbackup_db(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript("""
        CREATE TABLE clients (id INTEGER PRIMARY KEY, name TEXT, delete_pending INTEGER);
        CREATE TABLE backups (id INTEGER PRIMARY KEY, clientid INTEGER, backuptime DATE,
                              complete INTEGER, delete_pending INTEGER);
        CREATE TABLE backup_images (id INTEGER PRIMARY KEY, clientid INTEGER, backuptime DATE,
                                    complete INTEGER, delete_pending INTEGER);
        INSERT INTO clients VALUES (1,'ignored',NULL),(2,'DESKTOP-KVBHJ3P',NULL),
            (3,'budlap-1712',NULL),(4,'gone',1),(5,'never',NULL),(6,'imgonly',NULL);
        INSERT INTO backups VALUES (1,1,'2026-10-01 10:00:00',1,NULL),
            (2,2,'2026-10-18 20:48:04',1,NULL),
            (3,3,'2026-10-20 19:12:53',1,NULL),
            (4,3,'2026-10-20 19:30:00',0,NULL),
            (5,3,'2026-10-20 19:40:00',1,1),
            (6,4,'2026-10-20 19:00:00',1,NULL);
        INSERT INTO backup_images VALUES (1,2,'2026-10-19 08:00:00',1,NULL),
            (2,6,'2026-10-17 08:00:00',1,''),(3,3,'2026-10-20 19:20:00',1,NULL);
        """)
        return conn

    def test_urbackup_query(self):
        res = agent.query_urbackup(self._urbackup_db(), {"ignored"})
        self.assertEqual(set(res), {"DESKTOP-KVBHJ3P", "budlap-1712", "imgonly"})
        # newest completed, not pending delete; image backup counts too
        self.assertTrue(res["budlap-1712"].startswith(
            agent.urbackup_time_to_iso("2026-10-20 19:20:00")[:16]))
        self.assertTrue(res["DESKTOP-KVBHJ3P"].startswith(
            agent.urbackup_time_to_iso("2026-10-19 08:00:00")[:16]))

    def test_urbackup_time_is_utc(self):
        with mock.patch.dict(os.environ, {"TZ": "Europe/Budapest"}):
            import time
            time.tzset()
            try:
                self.assertEqual(agent.urbackup_time_to_iso("2026-10-20 19:12:53"),
                                 "2026-10-20T21:12:53+02:00")
            finally:
                os.environ.pop("TZ", None)
                time.tzset()


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = agent.validate_config({})
        self.assertEqual(cfg["mqtt_port"], 1883)

    def test_example_file_is_valid(self):
        with open(os.path.join(HERE, "..", "files", "nas-ha-agent.json.example")) as fh:
            cfg = agent.validate_config(json.load(fh))
        self.assertEqual(cfg["mqtt_password"], "CHANGE_ME")

    def test_invalid_values(self):
        bad = [{"mqtt_port": 0}, {"mqtt_port": "1883"}, {"mqtt_port": True},
               {"mqtt_host": ""}, {"units": ["-evil"]}, {"units": "smb"},
               {"units": ["a b"]}, {"filesystems": {"Data": "/data"}},
               {"filesystems": {"data": "relative"}}, {"smart_interval_s": -1},
               {"urbackup_ignore_clients": "x"}, {"cache_dir": 5}, []]
        for raw in bad:
            with self.assertRaises(agent.ConfigError, msg=repr(raw)):
                agent.validate_config(raw)

    def test_missing_file(self):
        with mock.patch.object(agent, "log"):
            self.assertEqual(agent.load_config("/nonexistent/x.json", True)["mqtt_port"], 1883)
            with self.assertRaises(agent.ConfigError):
                agent.load_config("/nonexistent/x.json", False)

    def test_bad_json(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json") as fh:
            fh.write("{nope")
            fh.flush()
            with self.assertRaises(agent.ConfigError):
                agent.load_config(fh.name, False)


class FakeRun:
    """Replacement for agent.run_cmd driven by a table keyed on argv prefix."""

    def __init__(self, table):
        self.table = table

    def __call__(self, args, timeout=20):
        for prefix, out in self.table.items():
            if tuple(args[:len(prefix)]) == prefix:
                if isinstance(out, Exception):
                    raise out
                return out
        raise RuntimeError("%s not found" % args[0])


def full_table():
    return {
        ("btrfs", "device", "stats"): fixture("btrfs_device_stats.txt"),
        ("btrfs", "scrub", "status"): fixture("btrfs_scrub_status.txt"),
        ("lsblk",): fixture("lsblk.txt"),
        ("smartctl",): fixture("smartctl_sda.json"),
        ("systemctl", "is-active"): "active\ninactive\n",
        ("docker", "ps"): "plex\nswag\nmy-sick.app\n",
        ("docker", "inspect"): fixture("docker_inspect.json"),
        ("smbstatus",): fixture("smbstatus.json"),
    }


def make_cfg(tmp, **over):
    cfg = agent.validate_config({
        "units": ["smb", "transmission-daemon"],
        "filesystems": {"data": tmp, "system": tmp},
        "urbackup_db": os.path.join(tmp, "missing.db"),
        "cache_dir": os.path.join(tmp, "cache"),
    })
    cfg.update(over)
    return cfg


class CollectionTests(unittest.TestCase):
    def collect(self, table, cache=None, now=1000.0):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(agent, "run_cmd", FakeRun(table)), \
                mock.patch.object(agent, "log"):
            col = agent.collect_all(make_cfg(tmp), {} if cache is None else cache, now)
        return col

    def test_everything_works_except_urbackup(self):
        col = self.collect(full_table())
        self.assertEqual(col.failed, {"urbackup"})  # db file missing
        s = col.state
        self.assertEqual(s["btrfs_data_errors"], 7)
        self.assertEqual(s["btrfs_system_errors"], 7)
        self.assertIn("btrfs_data_last_scrub", s)
        self.assertNotIn("btrfs_system_last_scrub", s)
        self.assertEqual(s["service_smb"], "ON")
        self.assertEqual(s["service_transmission_daemon"], "OFF")
        self.assertEqual(s["container_plex"], "ON")
        self.assertEqual(s["container_my_sick_app"], "OFF")
        self.assertEqual(s["containers_total"], 4)
        self.assertEqual(s["containers_running"], 3)
        self.assertEqual(s["containers_attr"]["plex"]["health"], "healthy")
        self.assertEqual(s["disk_sdb_temperature"], 42)
        self.assertEqual(s["samba_sessions"], 2)
        self.assertEqual(s["reboot_required"] in ("ON", "OFF"), True)
        self.assertIn("agent_last_report", s)
        self.assertIn("data_used_percent", s)

    def test_one_collector_fails_rest_publish(self):
        table = full_table()
        table[("docker", "ps")] = RuntimeError("docker not found")
        table[("smbstatus",)] = "garbage"
        col = self.collect(table)
        self.assertTrue({"containers", "samba"} <= col.failed)
        self.assertNotIn("containers_total", col.state)
        self.assertNotIn("samba_sessions", col.state)
        self.assertEqual(col.state["btrfs_data_errors"], 7)
        self.assertEqual(col.state["service_smb"], "ON")
        self.assertIn("disk_sda_smart", col.state)

    def test_lsblk_failure_marks_smart_group(self):
        table = full_table()
        table[("lsblk",)] = RuntimeError("boom")
        col = self.collect(table)
        self.assertIn("smart", col.failed)
        self.assertNotIn("disk_sda_smart", col.state)
        self.assertIn("service_smb", col.state)

    def test_smart_cache_used_within_interval(self):
        cache = {}
        self.collect(full_table(), cache, now=1000.0)
        self.assertEqual(set(cache), {"sda", "sdb", "sdc"})
        table = full_table()
        table[("smartctl",)] = RuntimeError("must not be called")
        col = self.collect(table, cache, now=1000.0 + 600)
        self.assertEqual(col.state["disk_sda_temperature"], 42)
        self.assertNotIn("smart:sda", col.failed)
        col = self.collect(table, cache, now=1000.0 + 1801)
        self.assertIn("smart:sda", col.failed)

    def test_smartctl_failure_negative_cache(self):
        cache = {}
        table = full_table()
        table[("smartctl",)] = RuntimeError("boom")
        col = self.collect(table, cache, now=1000.0)
        self.assertEqual(cache["sda"], {"ts": 1000.0, "failed": True})
        self.assertNotIn("disk_sda_smart", col.state)
        self.assertIn("smart:sda", col.failed)
        # within the interval smartctl is not retried and still no data
        table[("smartctl",)] = AssertionError("must not be called")
        col = self.collect(table, cache, now=1000.0 + 60)
        self.assertNotIn("disk_sda_smart", col.state)
        self.assertIn("smart:sda", col.failed)
        # after the interval it is retried and recovers
        col = self.collect(full_table(), cache, now=1000.0 + 1801)
        self.assertIn("disk_sda_smart", col.state)
        self.assertNotIn("smart:sda", col.failed)

    def test_disk_gone_dropped_from_cache(self):
        cache = {}
        self.collect(full_table(), cache)
        table = full_table()
        table[("lsblk",)] = "sda disk\n"
        self.collect(table, cache)
        self.assertEqual(set(cache), {"sda"})


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(agent, "run_cmd", FakeRun(full_table())), \
                mock.patch.object(agent, "log"):
            self.col = agent.collect_all(make_cfg(tmp), {}, 1000.0)
        self.payload, self.live = agent.build_discovery(self.col.comps, set(), {})
        self.cmps = self.payload["cmps"]

    def test_header(self):
        p = self.payload
        self.assertEqual(p["dev"], {"ids": ["nas_agent"], "name": "NAS", "mf": "HPE",
                                    "mdl": "ProLiant MicroServer Gen10"})
        self.assertEqual(p["o"], {"name": "nas-ha-agent", "sw": "1.0"})
        self.assertEqual(p["state_topic"], "nas/state")

    def test_every_component_consistent(self):
        for key, c in self.cmps.items():
            entity = c["default_entity_id"]
            self.assertEqual(c["unique_id"], entity.replace(".", "_"))
            domain = "binary_sensor" if c["p"] == "binary_sensor" else "sensor"
            self.assertTrue(entity.startswith(domain + ".nas_"))
            self.assertEqual(key, "nas_" + entity.split(".nas_", 1)[1])
            self.assertEqual(c["value_template"],
                             "{{ value_json['%s'] }}" % key[len("nas_"):])
            if key == "nas_agent_last_report":
                self.assertNotIn("expire_after", c)
                self.assertEqual(c["entity_category"], "diagnostic")
            else:
                self.assertEqual(c["expire_after"], 300)
            # state key must exist for every component
            self.assertIn(key[len("nas_"):], self.col.state)

    def test_specific_components(self):
        c = self.cmps
        self.assertEqual(c["nas_data_used_percent"]["default_entity_id"],
                         "sensor.nas_data_used_percent")
        self.assertEqual(c["nas_data_free"]["device_class"], "data_size")
        self.assertEqual(c["nas_data_free"]["unit_of_measurement"], "GiB")
        self.assertEqual(c["nas_btrfs_data_last_scrub"]["device_class"], "timestamp")
        self.assertEqual(c["nas_disk_sda_smart"]["device_class"], "problem")
        self.assertEqual(c["nas_disk_sda_temperature"]["device_class"], "temperature")
        self.assertEqual(c["nas_disk_sda_power_on_hours"]["entity_category"], "diagnostic")
        self.assertEqual(c["nas_service_transmission_daemon"]["device_class"], "running")
        self.assertEqual(c["nas_reboot_required"]["icon"], "mdi:restart-alert")
        self.assertNotIn("device_class", c["nas_reboot_required"])
        cont = c["nas_container_my_sick_app"]
        self.assertEqual(cont["json_attributes_topic"], "nas/state")
        self.assertEqual(cont["json_attributes_template"],
                         "{{ value_json['containers_attr']['my_sick_app'] | tojson }}")
        self.assertEqual(cont["default_entity_id"], "binary_sensor.nas_container_my_sick_app")
        self.assertIn("nas_containers_running", c)
        self.assertIn("nas_samba_sessions", c)

    def test_state_is_json_serialisable(self):
        json.dumps(self.col.state)
        json.dumps(self.payload)

    def test_removal_payload(self):
        prev = dict(self.live)
        comps = {k: v for k, v in self.col.comps.items() if k != "container_swag"}
        payload, live = agent.build_discovery(comps, set(), prev)
        self.assertEqual(payload["cmps"]["nas_container_swag"], {"p": "binary_sensor"})
        self.assertNotIn("container_swag", live)
        # next round the removal is gone again
        payload2, _ = agent.build_discovery(comps, set(), live)
        self.assertNotIn("nas_container_swag", payload2["cmps"])

    def test_failed_group_keeps_previous_components(self):
        comps = {k: v for k, v in self.col.comps.items() if v[0] != "containers"}
        payload, live = agent.build_discovery(comps, {"containers"}, dict(self.live))
        self.assertIn("nas_container_swag", payload["cmps"])
        self.assertEqual(payload["cmps"]["nas_container_swag"]["p"], "binary_sensor")
        self.assertIn("default_entity_id", payload["cmps"]["nas_container_swag"])
        self.assertIn("container_swag", live)

    def test_failed_parent_group_covers_children(self):
        comps = {k: v for k, v in self.col.comps.items() if not v[0].startswith("smart")}
        payload, _ = agent.build_discovery(comps, {"smart"}, dict(self.live))
        self.assertIn("default_entity_id", payload["cmps"]["nas_disk_sda_smart"])

    def test_need_discovery(self):
        digest = agent.payload_hash(self.payload)
        meta = {"hash": digest, "ts": 1000.0}
        self.assertFalse(agent.need_discovery(digest, meta, 1100.0, False))
        self.assertTrue(agent.need_discovery(digest, meta, 1100.0, True))
        self.assertTrue(agent.need_discovery(digest, meta, 1000.0 + 3600, False))
        self.assertTrue(agent.need_discovery("other", meta, 1100.0, False))
        self.assertTrue(agent.need_discovery(digest, {}, 1100.0, False))


class MainTests(unittest.TestCase):
    def run_main(self, argv, table=None):
        out = []
        with mock.patch.object(agent, "run_cmd", FakeRun(table or full_table())), \
                mock.patch.object(agent, "log"), \
                mock.patch("builtins.print", lambda *a, **k: out.append(a[0])):
            rc = agent.main(argv)
        return rc, out

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "c.json")
            cache = os.path.join(tmp, "cache")
            with open(cfg, "w") as fh:
                json.dump({"cache_dir": cache, "urbackup_db": os.path.join(tmp, "x.db"),
                           "filesystems": {"data": tmp}}, fh)
            rc, out = self.run_main(["--dry-run", "--config", cfg])
            self.assertEqual(rc, 0)
            data = json.loads(out[0])
            self.assertIn("discovery", data)
            self.assertIn("data_used_percent", data["state"])
            self.assertFalse(os.path.exists(cache))

    def test_dry_run_missing_config(self):
        rc, out = self.run_main(["--dry-run", "--config", "/nonexistent/c.json"])
        self.assertEqual(rc, 0)
        self.assertIn("state", json.loads(out[0]))

    def test_missing_config_without_dry_run(self):
        rc, _ = self.run_main(["--config", "/nonexistent/c.json"])
        self.assertEqual(rc, 2)

    def _publish_run(self, tmp, extra=()):
        cfg = os.path.join(tmp, "c.json")
        with open(cfg, "w") as fh:
            json.dump({"cache_dir": os.path.join(tmp, "cache"),
                       "urbackup_db": os.path.join(tmp, "x.db"),
                       "filesystems": {"data": tmp}}, fh)
        published = []

        class Fake:
            def __init__(self, *a, **k):
                pass

            def connect(self):
                pass

            def publish(self, topic, payload, retain=False):
                published.append((topic, retain, json.loads(payload)))

            def disconnect(self):
                pass

            def close(self):
                pass

        with mock.patch.object(agent, "MqttClient", Fake):
            rc, _ = self.run_main(["--config", cfg] + list(extra))
        return rc, published

    def test_publish_and_discovery_caching(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, pub = self._publish_run(tmp)
            self.assertEqual(rc, 0)
            self.assertEqual([(t, r) for t, r, _ in pub],
                             [(agent.DISCOVERY_TOPIC, True), ("nas/state", False)])
            self.assertTrue(os.path.exists(os.path.join(tmp, "cache", "discovery.json")))
            # second run: unchanged -> state only (agent_last_report is state-only)
            _, pub = self._publish_run(tmp)
            self.assertEqual([t for t, _, _ in pub], ["nas/state"])
            _, pub = self._publish_run(tmp, ["--force-discovery"])
            self.assertEqual(len(pub), 2)

    def test_broker_unreachable_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "c.json")
            with open(cfg, "w") as fh:
                json.dump({"cache_dir": os.path.join(tmp, "cache"), "mqtt_port": 1,
                           "filesystems": {"data": tmp}}, fh)

            class Down:
                def __init__(self, *a, **k):
                    pass

                def connect(self):
                    raise agent.MqttError("down")

                def close(self):
                    pass

            with mock.patch.object(agent, "MqttClient", Down):
                rc, _ = self.run_main(["--config", cfg])
            self.assertEqual(rc, 0)
            self.assertFalse(os.path.exists(os.path.join(tmp, "cache", "discovery.json")))


if __name__ == "__main__":
    unittest.main()
