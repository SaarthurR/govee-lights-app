"""On-light scene checks: isolated state, mocked catalogs/sockets, no light traffic."""
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ['GOVEE_DRY'] = '1'
import server as app

PARAM = 'AyYAAQAKAgH/GQG0CgoCyBQF//8AAP//////AP//lP8AFAGWAAAAACMAAg8FAgH/FAH7AAAB+goEBP8AtP8AR///4/8AAAAAAAAAABoAAAABAgH/BQHIFBQC7hQBAP8AAAAAAAAAAA=='
VECTOR = ['a30001070203260001000a0201ff1901b40a0ad9', 'a30102c81405ffff0000ffffffffff00ffff9412',
          'a302ff00140196000000002300020f050201ff0a', 'a3031401fb000001fa0a0404ff00b4ff0047ffb3',
          'a304ffe3ff00000000000000001a00000001025d', 'a30501ff0501c8141402ee140100ff0000000092',
          'a3ff00000000000000000000000000000000005c', '330504d4000000000000000000000000000000e6']
CATALOG = {'status': 200, 'data': {'categories': [
    {'categoryName': 'Nature', 'scenes': [
        {'sceneName': 'Forest', 'lightEffects': [{'scenceParam': PARAM, 'sceneCode': 212}]},
        {'sceneName': 'No bytes', 'lightEffects': [{'sceneCode': 1}]}]},
    {'categoryName': 'Empty', 'scenes': []}]}}


class SceneChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.save = patch.object(app, 'SAVE', Path(self.tmp.name) / 'state.json'); self.save.start()
        self.saved = {k: getattr(app, k) for k in ('lights', 'st', 'schedule', 'themes', 'found', 'paused', 'scene_cache')}
        for k in self.saved:
            setattr(app, k, set() if k == 'paused' else {})
        for sku in ('H805D', 'UNKNOWN'):
            app.add_light(sku, sku, sku, '192.0.2.1')
            app.scene_cache[sku] = {'categories': app.filter_scene_catalog(CATALOG), 'offline': False}

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(app, k, v)
        self.save.stop(); self.tmp.cleanup()

    def test_forest_vector_and_boundaries(self):
        self.assertEqual([base64.b64decode(p).hex() for p in app.encode_scene(PARAM, 212)], VECTOR)
        for size in (1, 14, 15, 31, 32, 48):
            raw = bytes(range(size))
            packets = [base64.b64decode(p) for p in app.encode_scene(base64.b64encode(raw), 1)]
            self.assertTrue(all(len(p) == 20 and app.checksum(p) == 0 for p in packets))
            self.assertEqual(packets[-2][1], 255)
            self.assertEqual((packets[0][5:19] + b''.join(p[2:19] for p in packets[1:-1]))[:size], raw)
        for param, code in (('', 1), ('?', 1), (PARAM, True), (PARAM, 65536)):
            with self.assertRaises(ValueError):
                app.encode_scene(param, code)

    def test_filter_and_any_sku(self):
        self.assertEqual(app.scenes_for_light('UNKNOWN')['categories'], [
            {'name': 'Nature', 'scenes': ['Forest']}, {'name': 'Empty', 'scenes': []}])
        app.scene_cache['UNKNOWN']['categories'][0]['scenes'][0]['code'] = 213
        with patch.object(app, 'send') as send:
            app.play_scene(['H805D', 'UNKNOWN'], 'Forest')
            self.assertEqual(send.call_count, 2)
            self.assertEqual(send.call_args_list[1].args[2], {'command': app.encode_scene(PARAM, 213)})
        app.scene_cache['UNKNOWN']['categories'][0]['scenes'] = []
        self.assertEqual(app.scenes_for_light('all')['categories'][0]['scenes'], [])
        with patch.object(app, 'send') as send:
            with self.assertRaises(ValueError):
                app.play_scene(['H805D', 'UNKNOWN'], 'Forest')
            send.assert_not_called()

    def test_play_pauses_only_target_and_normal_look_resumes(self):
        with patch.object(app, 'send') as send:
            app.play_scene(['H805D'], 'Forest')
            send.assert_called_once_with('H805D', 'ptReal', {'command': app.encode_scene(PARAM, 212)})
        self.assertEqual(app.paused, {'H805D'})
        with patch.object(app, 'send') as send, patch.object(app.time, 'sleep', side_effect=InterruptedError):
            with self.assertRaises(InterruptedError):
                app.streamer()
            send.assert_not_called()  # other light has no streaming capability
        app.push('H805D', {'effect': 'wave'})
        self.assertNotIn('H805D', app.paused)
        self.assertIsNone(app.st['H805D']['scene'])
        app.undo('H805D')
        self.assertEqual(app.st['H805D']['scene'], 'Forest')
        self.assertIn('H805D', app.paused)

    def test_dry_socket_and_saved_state(self):
        with patch.object(app, 'sock') as sock:
            app.play_scene(['H805D', 'UNKNOWN'], 'Forest')
            app.razer('H805D', app.ARM)
            sock.sendto.assert_not_called()
        app.load()
        for key in app.lights:
            self.assertEqual(app.st[key]['effect'], 'scene')
            self.assertEqual(app.st[key]['scene'], 'Forest')
            app.push(key, app.light_settings(key, {'brightness': 72, 'on': False}))
            self.assertEqual(app.st[key]['effect'], 'scene')
            self.assertIn(key, app.paused)
        app.save()
        app.paused.clear(); app.load()
        self.assertEqual(app.paused, set(app.lights))

    def test_stale_frame_cannot_send_and_failure_keeps_look(self):
        with patch.object(app, 'DRY', False), patch.object(app, 'lan_gate'), patch.object(app, 'sock') as sock:
            app.play_scene(['H805D'], 'Forest')
            app.razer('H805D', app.ARM)
            self.assertEqual(sock.sendto.call_count, 1)
            self.assertEqual(json.loads(sock.sendto.call_args.args[0])['msg']['cmd'], 'ptReal')
        app.push('H805D', {'effect': 'wave'})
        with patch.object(app, 'send', side_effect=OSError):
            with self.assertRaises(OSError):
                app.play_scene(['H805D'], 'Forest')
        self.assertNotIn('H805D', app.paused)
        self.assertEqual(app.st['H805D']['effect'], 'wave')

    def test_disk_cache_offline_and_missing(self):
        app.scene_cache.clear()
        with patch.object(app.request, 'urlopen') as fetch:
            fetch.return_value.__enter__.return_value.read.return_value = json.dumps(CATALOG).encode()
            self.assertFalse(app.scene_catalog('ANY-SKU')['offline'])
            self.assertIn('sku=ANY-SKU', fetch.call_args.args[0].full_url)
            app.scene_catalog('ANY-SKU')
            self.assertEqual(fetch.call_count, 1)
        app.scene_cache.clear()
        with patch.object(app.request, 'urlopen', side_effect=OSError):
            cached = app.scene_catalog('ANY-SKU')
            self.assertTrue(cached['offline'])
            self.assertEqual(cached['categories'][0]['scenes'][0]['name'], 'Forest')
            self.assertEqual(app.scene_catalog('MISSING'), {'categories': [], 'offline': True})
