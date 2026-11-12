import json
import struct
import tempfile
import unittest
from pathlib import Path

from meshingup.agent3d import contract, validate_shots
from meshingup.avatar3d import animate_avatar3d, create_avatar_project, enhance_avatar3d, finger_bone_names, inspect_glb, validate_enhance_spec, validate_project_avatar
from meshingup.project import ProjectError, load_project, save_project


def write_glb(path, document):
    payload = json.dumps(document).encode()
    payload += b" " * (-len(payload) % 4)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 20 + len(payload)) + struct.pack("<I4s", len(payload), b"JSON") + payload)


def rigged_document(target_names=None):
    mesh = {"primitives": [{"attributes": {"POSITION": 0}}]}
    if target_names:
        mesh["extras"] = {"targetNames": target_names}
    return {"asset": {"version": "2.0"}, "nodes": [{"name": "head"}, {"name": "hairBack"}, {"mesh": 0, "skin": 0}],
            "meshes": [mesh], "skins": [{"joints": [0, 1]}], "animations": [{"name": "Idle"}]}


FACE = {"bone": "head", "eyes": [{"name": "left", "center": [0.1, 0.4], "radius": [0.05, 0.05]},
                                 {"name": "right", "center": [-0.1, 0.4], "radius": [0.05, 0.05]}],
        "mouth": {"center": [0, 0.3], "width": 0.07}}
HAIR = {"parent": "head", "bones": [{"name": "hairSide", "head": [0, 0.3, 0.4], "tail": [0, 0.3, 0.1]}]}


FINGER = {"name": "leftIndex", "radius": 0.01, "points": [[0.3, 0, -0.2], [0.3, 0, -0.22], [0.3, 0, -0.24], [0.3, 0, -0.26]]}
HANDS = [{"bone": "head", "fingers": [FINGER], "collapse": [{"point": [0.3, 0, -0.2], "normal": [0, 0, -1], "radius": 0.08}]}]


class AvatarOnlyValidationTests(unittest.TestCase):
    def test_avatar_only_project_has_no_2d_pending_work(self):
        from meshingup.project import validate_project
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "model.glb"
            write_glb(model, rigged_document())
            project = root / "p.meshingup"
            create_avatar_project(model, project)
            result = validate_project(project)
            self.assertEqual(result["errors"], [])
            self.assertFalse([w for w in result["warnings"] if "pivotes" in w or "capas" in w or "clips" in w or "expresiones" in w], result["warnings"])

    def test_expressions_option_is_validated(self):
        self.assertEqual(validate_enhance_spec({"face": dict(FACE, expressions=["happy", "sad"])})["face"]["expressions"], ["happy", "sad"])
        self.assertEqual(validate_enhance_spec({"hands": [dict(HANDS[0], palm_normal=[1, 0, 0])]})["hands"][0]["palm_normal"], [1, 0, 0])
        for bad in (["joy"], ["happy", "happy"], "happy"):
            with self.subTest(bad=bad), self.assertRaises(ProjectError):
                validate_enhance_spec({"face": dict(FACE, expressions=bad)})
        with self.assertRaises(ProjectError):
            validate_enhance_spec({"hands": [dict(HANDS[0], palm_normal=[0, 0, 0])]})


class HandsSpecTests(unittest.TestCase):
    def test_accepts_hands_alone_and_names_bones_like_vrm(self):
        clean = validate_enhance_spec({"hands": [dict(HANDS[0], script="x", skin_sample=[0.3, 0, -0.23])]})
        self.assertEqual(set(clean), {"hands"})
        self.assertNotIn("script", clean["hands"][0])
        self.assertEqual(clean["hands"][0]["fingers"][0]["points"][3], [0.3, 0, -0.26])
        self.assertEqual(finger_bone_names("leftIndex"), ["leftIndexProximal", "leftIndexIntermediate", "leftIndexDistal"])
        self.assertEqual(finger_bone_names("rightThumb"), ["rightThumbMetacarpal", "rightThumbProximal", "rightThumbDistal"])

    def test_rejects_malformed_hands(self):
        invalid = [
            [], [{"bone": "head", "fingers": []}], [dict(HANDS[0], bone="1x")],
            [dict(HANDS[0], fingers=[dict(FINGER, radius=0)])],
            [dict(HANDS[0], fingers=[dict(FINGER, points=FINGER["points"][:3])])],
            [dict(HANDS[0], fingers=[dict(FINGER, points=[FINGER["points"][0]] * 4)])],
            [dict(HANDS[0], fingers=[FINGER, FINGER])],
            [dict(HANDS[0], collapse=[{"point": [0, 0, 0], "normal": [0, 0, 0], "radius": 1}])],
            [dict(HANDS[0], collapse=[{"point": [0, 0, 0], "normal": [0, 0, 1], "radius": -1}])],
            [dict(HANDS[0], skin_sample=[0, 0])],
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ProjectError):
                validate_enhance_spec({"hands": value})

    def test_finger_bones_must_not_exist_and_hand_bone_must(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "model.glb"
            document = rigged_document()
            document["nodes"].insert(2, {"name": "leftIndexProximal"})
            document["nodes"][3] = {"mesh": 0, "skin": 0}
            document["skins"][0]["joints"] = [0, 1, 2]
            write_glb(model, document)
            project = root / "p.meshingup"
            create_avatar_project(model, project)
            fake_blender = str(root / "no-blender")
            with self.assertRaisesRegex(ProjectError, "leftIndexProximal"):
                enhance_avatar3d(project, {"hands": HANDS}, blender_path=fake_blender)
            with self.assertRaisesRegex(ProjectError, "no contiene el hueso `leftHand`"):
                enhance_avatar3d(project, {"hands": [dict(HANDS[0], bone="leftHand", fingers=[dict(FINGER, name="leftRing")])]}, blender_path=fake_blender)


class MorphKeyframeTests(unittest.TestCase):
    def test_morph_keys_are_validated_before_running_blender(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "model.glb"
            write_glb(model, rigged_document(["blink", "aa"]))
            project = root / "p.meshingup"
            create_avatar_project(model, project)
            fake_blender = str(root / "no-blender")
            head = {"frame": 0, "bone": "head", "rotation": [0, 0, 0]}
            cases = [
                ([head, {"frame": 2, "morph": "oh", "value": 1}], "no contiene el morph"),
                ([head, {"frame": 2, "morph": "aa", "value": 1.5}], "entre 0 y 1"),
                ([head, {"frame": 2, "morph": "aa", "value": True}], "entre 0 y 1"),
                ([head, {"frame": 2, "morph": "aa", "value": 1, "bone": "head"}], "no ambos"),
                ([head, {"frame": 2, "morph": "aa", "value": 1}, {"frame": 2, "morph": "aa", "value": 0}], "más de una clave"),
                ([head, {"frame": 99, "morph": "aa", "value": 1}], "fotograma"),
            ]
            for keyframes, message in cases:
                with self.subTest(message=message), self.assertRaisesRegex(ProjectError, message):
                    animate_avatar3d(project, "Talk", 10, 24, keyframes, blender_path=fake_blender)
            # A valid mix reaches the Blender launch (which fails here) instead of a validation error.
            with self.assertRaisesRegex(ProjectError, "Blender"):
                animate_avatar3d(project, "Talk", 10, 24, [head, {"frame": 2, "morph": "aa", "value": 0.8}], blender_path=fake_blender)


class EnhanceSpecTests(unittest.TestCase):
    def test_accepts_face_and_secondary_bones_and_drops_unknown_fields(self):
        clean = validate_enhance_spec({"face": dict(FACE, script="import os"), "secondary_bones": dict(HAIR, save_blend="/tmp/x"), "save_blend": "/tmp/y"})
        self.assertEqual(set(clean), {"face", "secondary_bones"})
        self.assertNotIn("script", clean["face"])
        self.assertNotIn("save_blend", clean["secondary_bones"])
        self.assertEqual(clean["secondary_bones"]["bones"][0]["name"], "hairSide")

    def test_rejects_malformed_requests(self):
        invalid = [
            None, {}, {"face": {}}, {"face": {"eyes": [{"center": [0, 0], "radius": [0, 1]}]}},
            {"face": {"eyes": [{"center": [0, float("nan")], "radius": [1, 1]}]}},
            {"face": {"eyes": [{"center": [0, True], "radius": [1, 1]}]}},
            {"face": {"mouth": {"center": [0, 0], "width": -1}}},
            {"face": dict(FACE, bone="head; rm")},
            {"secondary_bones": {"bones": []}},
            {"secondary_bones": {"bones": [{"name": "hair", "head": [0, 0, 0], "tail": [0, 0, 0]}]}},
            {"secondary_bones": {"bones": [{"name": "1bad", "head": [0, 0, 0], "tail": [0, 0, 1]}]}},
            {"secondary_bones": {"bones": HAIR["bones"] * 2}},
            {"secondary_bones": dict(HAIR, smooth=1.5)},
            {"secondary_bones": dict(HAIR, color_like={"point": [0, 0]})},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ProjectError):
                validate_enhance_spec(value)


class EnhanceProjectTests(unittest.TestCase):
    def test_morph_targets_are_inspected(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.glb"
            write_glb(model, rigged_document(["blink", "aa"]))
            self.assertEqual(inspect_glb(model)["morph_targets"], ["aa", "blink"])
            write_glb(model, rigged_document())
            self.assertEqual(inspect_glb(model)["morph_targets"], [])

    def test_rejects_conflicts_before_running_blender(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "model.glb"
            write_glb(model, rigged_document(["blink"]))
            project = root / "p.meshingup"
            create_avatar_project(model, project)
            fake_blender = str(root / "no-blender")
            with self.assertRaisesRegex(ProjectError, "morph targets"):
                enhance_avatar3d(project, {"face": FACE}, blender_path=fake_blender)
            with self.assertRaisesRegex(ProjectError, "ya contiene"):
                enhance_avatar3d(project, {"secondary_bones": {"parent": "head", "bones": [dict(HAIR["bones"][0], name="hairBack")]}}, blender_path=fake_blender)
            with self.assertRaisesRegex(ProjectError, "no contiene el hueso"):
                enhance_avatar3d(project, {"secondary_bones": dict(HAIR, parent="tailRoot")}, blender_path=fake_blender)
            self.assertNotIn("enhancements", load_project(project)["avatar3d"])

    def test_reconstructions_block_ready_until_reviewed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "model.glb"
            write_glb(model, rigged_document(["blink"]))
            project = root / "p.meshingup"
            create_avatar_project(model, project)
            data = load_project(project)
            data["avatar3d"]["enhancements"] = [{"reconstruction": True}]
            save_project(project, data, action="test", actor="test")
            data = load_project(project)
            self.assertTrue(any("reconstrucciones" in item for item in validate_project_avatar(data, project)["warnings"]))
            data["agent3d"] = {"review": {"source_sha256": "stale", "verdict": "pass"}}
            self.assertTrue(any("reconstrucciones" in item for item in validate_project_avatar(data, project)["warnings"]))
            data["agent3d"] = {"review": {"source_sha256": data["avatar3d"]["sha256"], "verdict": "pass"}}
            self.assertFalse(any("reconstrucciones" in item for item in validate_project_avatar(data, project)["warnings"]))


class EvidenceShotTests(unittest.TestCase):
    def test_contract_documents_shots(self):
        self.assertIn("shots", contract()["evidence_schema"])

    def test_accepts_poses_morphs_and_clip_frames(self):
        validate_shots([])
        validate_shots([{"name": "arms", "view": "front_left", "pose": {"leftUpperArm": [0, -60, 0]}, "morphs": {"blink": 1}},
                        {"name": "idle_12", "clip": {"name": "Idle", "frame": 12}, "center": [0, 0, 0], "scale": 1.5}])

    def test_rejects_malformed_shots(self):
        base = {"name": "a"}
        invalid = [
            None, [None], [{}], [dict(base, name="../x")], [dict(base, name="front")], [base, base],
            [dict(base, view="top")], [dict(base, pose={"head": [0, 0]})], [dict(base, pose={"head": [0, 0, float("inf")]})],
            [dict(base, morphs={"blink": 2})], [dict(base, morphs={"blink": True})],
            [dict(base, clip={"name": "Idle", "frame": -1})], [dict(base, clip={"name": "Idle", "frame": 1}, pose={"head": [0, 0, 1]})],
            [dict(base, scale=0)], [dict(base, center=[0, 0])], [dict(base, name=f"s{i}") for i in range(25)],
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ProjectError):
                validate_shots(value)


if __name__ == "__main__":
    unittest.main()
