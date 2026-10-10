"""The RPC methods.  The Unix socket (GTK app, CLI) and the web interface's HTTP API both dispatch here, so every
client has exactly the same capabilities and sees the same torrents and settings."""

import collections
import os
import secrets
import time

from . import __version__, netif, paths, schema, util
from .engine import EngineError

LOCAL_ONLY = {"daemon.shutdown"}


class ApiError(Exception):
    pass


class Api:
    def __init__(self, engine, settings, webui=None, stop=None):
        self.engine = engine
        self.settings = settings
        self.webui = webui
        self.stop = stop
        self.methods = {
            "ping": self.ping,
            "sync": self.sync,
            "session.status": self.session_status,
            "torrents.list": self.torrents_list,
            "torrents.details": self.torrents_details,
            "torrents.inspect": self.torrents_inspect,
            "torrents.add": self.torrents_add,
            "torrents.pause": self.torrents_pause,
            "torrents.resume": self.torrents_resume,
            "torrents.force_start": self.torrents_force_start,
            "torrents.recheck": self.torrents_recheck,
            "torrents.reannounce": self.torrents_reannounce,
            "torrents.remove": self.torrents_remove,
            "torrents.move": self.torrents_move,
            "torrents.queue": self.torrents_queue,
            "torrents.set_options": self.torrents_set_options,
            "torrents.set_files": self.torrents_set_files,
            "torrents.magnet": self.torrents_magnet,
            "torrents.export": self.torrents_export,
            "trackers.add": self.trackers_add,
            "trackers.remove": self.trackers_remove,
            "trackers.edit": self.trackers_edit,
            "peers.add": self.peers_add,
            "settings.schema": self.settings_schema,
            "settings.get": self.settings_get,
            "settings.set": self.settings_set,
            "settings.reset": self.settings_reset,
            "action.run": self.action_run,
            "port.randomize": self.port_randomize,
            "alt_speed.set": self.alt_speed_set,
            "net.interfaces": self.net_interfaces,
            "labels.list": self.labels_list,
            "labels.set": self.labels_set,
            "labels.remove": self.labels_remove,
            "ipfilter.update": self.ipfilter_update,
            "webui.status": self.webui_status,
            "webui.enable": self.webui_enable,
            "events.poll": self.events_poll,
            "fs.list": self.fs_list,
            "fs.free_space": self.fs_free_space,
            "create.start": self.create_start,
            "create.status": self.create_status,
            "stats.history": self.stats_history,
            "log.tail": self.log_tail,
            "daemon.info": self.daemon_info,
            "daemon.shutdown": self.daemon_shutdown,
        }

    def dispatch(self, method, params, origin="local"):
        fn = self.methods.get(method)
        if fn is None:
            raise ApiError("unknown method %r" % method)
        if origin != "local" and method in LOCAL_ONLY:
            raise ApiError("%s is only available on this computer" % method)
        if not isinstance(params, dict):
            raise ApiError("params must be an object")
        try:
            return fn(**params)
        except TypeError as e:
            if "argument" in str(e):
                raise ApiError("bad parameters for %s: %s" % (method, e))
            raise
        except EngineError as e:
            raise ApiError(str(e))

    # ---------------------------------------------------------------- general
    def ping(self):
        return {"version": __version__, "libtorrent": self.engine.lt_version}

    def sync(self, since=0, client=""):
        """Everything a window needs for one refresh, in one round trip."""
        if client == "gtk":
            self.engine.last_gui_poll = time.time()
        summaries = self.engine.summaries()
        counts, labels, trackers = self.engine.counts(summaries)
        status = self.engine.session_status(summaries)
        status["webui"] = self.webui_status()
        known = self.engine.labels()
        for name in known:
            labels.setdefault(name, 0)
        return {"session": status, "torrents": summaries, "counts": counts, "labels": labels, "trackers": trackers,
                "label_paths": {k: v.get("path", "") for k, v in known.items()},
                "events": self.engine.events_since(int(since or 0))}

    def session_status(self):
        st = self.engine.session_status()
        st["webui"] = self.webui_status()
        return st

    def daemon_info(self):
        return {"version": __version__, "pid": os.getpid(), "socket": paths.socket_path(),
                "config": paths.settings_file(), "data": paths.data_dir(), "log": paths.log_file()}

    def daemon_shutdown(self):
        if self.stop:
            self.stop.set()
        return True

    # ---------------------------------------------------------------- torrents
    def torrents_list(self):
        summaries = self.engine.summaries()
        counts, labels, trackers = self.engine.counts(summaries)
        return {"torrents": summaries, "counts": counts, "labels": labels, "trackers": trackers}

    def torrents_details(self, id, sections=None):
        return self.engine.details(self.engine.resolve(id), sections)

    def torrents_inspect(self, magnet=None, data=None, path=None, url=None):
        return self.engine.inspect(magnet, data, path, url)

    def torrents_add(self, magnet=None, data=None, path=None, url=None, options=None):
        return self.engine.add(magnet, data, path, url, options)

    def _ids(self, ids):
        out = self.engine.resolve_many(ids)
        if not out:
            raise ApiError("no torrents given")
        return out

    def torrents_pause(self, ids):
        self.engine.pause(self._ids(ids))
        return True

    def torrents_resume(self, ids):
        self.engine.resume(self._ids(ids))
        return True

    def torrents_force_start(self, ids):
        self.engine.resume(self._ids(ids), force=True)
        return True

    def torrents_recheck(self, ids):
        self.engine.recheck(self._ids(ids))
        return True

    def torrents_reannounce(self, ids):
        self.engine.reannounce(self._ids(ids))
        return True

    def torrents_remove(self, ids, delete_data=False):
        self.engine.remove(self._ids(ids), bool(delete_data))
        return True

    def torrents_move(self, ids, path):
        if not path:
            raise ApiError("no destination folder given")
        self.engine.move(self._ids(ids), path)
        return True

    def torrents_queue(self, ids, where):
        self.engine.queue(self._ids(ids), where)
        return True

    def torrents_set_options(self, ids, options):
        self.engine.set_options(self._ids(ids), options or {})
        return True

    def torrents_set_files(self, id, priorities):
        self.engine.set_file_priorities(self.engine.resolve(id), priorities)
        return True

    def torrents_magnet(self, id):
        return self.engine.magnet(self.engine.resolve(id))

    def torrents_export(self, id):
        return self.engine.export_torrent(self.engine.resolve(id))

    def trackers_add(self, id, urls):
        self.engine.trackers_add(self.engine.resolve(id), [urls] if isinstance(urls, str) else urls)
        return True

    def trackers_remove(self, id, urls):
        self.engine.trackers_remove(self.engine.resolve(id), [urls] if isinstance(urls, str) else urls)
        return True

    def trackers_edit(self, id, old, new):
        self.engine.trackers_edit(self.engine.resolve(id), old, new)
        return True

    def peers_add(self, id, address):
        self.engine.add_peer(self.engine.resolve(id), address)
        return True

    # ---------------------------------------------------------------- settings
    def settings_schema(self):
        return {"pages": schema.public_schema(), "sort": schema.SORT_LABELS, "filters": util.FILTERS,
                "states": util.STATE_LABELS, "mask": schema.MASK}

    def settings_get(self, keys=None):
        snap = self.settings.snapshot(masked=True)
        if keys:
            return {k: snap[k] for k in keys if k in snap}
        return snap

    def settings_set(self, values):
        notices = {}
        values = dict(values or {})
        enabling = values.get("webui.enabled") in (True, "true", "on", "1", 1, "yes")
        if enabling and not self.settings["webui.password"] and not values.get("webui.password"):
            pw = secrets.token_urlsafe(9)
            values["webui.password"] = pw
            notices["webui_password"] = pw
        changed, errors = self.settings.update(values)
        if errors:
            notices.pop("webui_password", None)
        if enabling and "webui.enabled" in changed:
            notices["webui"] = self.webui_status()
        return {"changed": changed, "errors": errors, "notices": notices}

    def settings_reset(self):
        changed, errors = self.settings.reset()
        return {"changed": changed, "errors": errors}

    def action_run(self, action):
        if action == "port.randomize":
            return {"message": "Listening on port %d" % self.port_randomize()}
        if action == "ipfilter.update":
            self.ipfilter_update()
            return {"message": "Loading the blocklist…"}
        if action == "settings.reset":
            self.settings_reset()
            return {"message": "All settings were reset"}
        raise ApiError("unknown action %r" % action)

    def port_randomize(self):
        return self.engine.randomize_port()

    def alt_speed_set(self, on="toggle"):
        return self.engine.set_alt_speed(on)

    def net_interfaces(self):
        return netif.interfaces()

    def labels_list(self):
        labels = self.engine.labels()
        counts = collections.Counter(m.get("label") for m in self.engine.meta.values() if m.get("label"))
        return {k: {"path": v.get("path", ""), "count": counts.get(k, 0)} for k, v in sorted(labels.items())}

    def labels_set(self, name, path=""):
        self.engine.label_set(name, path)
        return True

    def labels_remove(self, name):
        self.engine.label_remove(name)
        return True

    def ipfilter_update(self):
        return self.engine.ipfilter_update()

    # ---------------------------------------------------------------- web interface
    def webui_status(self):
        if self.webui is None:
            return {"enabled": self.settings["webui.enabled"], "running": False, "urls": [], "error": None}
        return self.webui.status()

    def webui_enable(self, on=True):
        return self.settings_set({"webui.enabled": bool(on)})

    # ---------------------------------------------------------------- misc
    def events_poll(self, since=0):
        return self.engine.events_since(int(since or 0))

    def fs_list(self, path="", show_hidden=False):
        """Folders on the machine running Cascade (the web interface's folder picker)."""
        p = os.path.abspath(os.path.expanduser(path or self.settings["downloads.save_path"] or "~"))
        while not os.path.isdir(p) and os.path.dirname(p) != p:
            p = os.path.dirname(p)
        try:
            names = sorted(os.listdir(p), key=str.lower)
        except OSError as e:
            raise ApiError("cannot open %s: %s" % (p, e.strerror))
        dirs = [n for n in names if (show_hidden or not n.startswith(".")) and os.path.isdir(os.path.join(p, n))]
        return {"path": p, "parent": os.path.dirname(p) if os.path.dirname(p) != p else None, "dirs": dirs,
                "home": os.path.expanduser("~"), "free": util.free_space(p)}

    def fs_free_space(self, path=""):
        return util.free_space(path or self.settings["downloads.save_path"])

    def create_start(self, path, trackers=None, piece_size=0, private=False, comment="", web_seeds=None, source="",
                     version="hybrid", output=None, seed=False):
        return self.engine.create_start(path, trackers, piece_size, private, comment, web_seeds, source, version,
                                        output, seed)

    def create_status(self, job):
        return self.engine.create_status(job)

    def stats_history(self):
        return [list(x) for x in self.engine.history]

    def log_tail(self, lines=200):
        try:
            with open(paths.log_file(), "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 256 * 1024))
                data = fh.read().decode("utf-8", "replace")
            return data.splitlines()[-int(lines):]
        except OSError:
            return []
