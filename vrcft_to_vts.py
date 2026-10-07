import asyncio
import json
import math
import mmap
import os
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer
from zeroconf import IPVersion, ServiceInfo, Zeroconf
import websockets


def _app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


OSC_IP = "127.0.0.1"
OSC_PORT = 9000
HTTP_PORT = 9002
VTS_URL = "ws://127.0.0.1:8001"
TOKEN_PATH = os.path.join(_app_dir(), "vts_token.json")
PLUGIN_NAME = "VRCFT Bridge"
PLUGIN_DEV = "local"

INVERT_YAW = False
INVERT_PITCH = False
INVERT_ROLL = False
HEAD_GAIN = 0.4
EYE_REST = 0.5
EYE_GAZE = 1.0
MOUTH_GAIN = 1.0
SMILE_REST = 0.5
BROW_GAIN = 1.0

PARAMS = [
    "v2/EyeLeftX", "v2/EyeLeftY", "v2/EyeRightX", "v2/EyeRightY", "v2/EyeX", "v2/EyeY",
    "v2/EyeLidLeft", "v2/EyeLidRight", "v2/EyeLid",
    "v2/EyeSquintLeft", "v2/EyeSquintRight", "v2/EyeSquint",
    "v2/PupilDilation",
    "v2/BrowPinchLeft", "v2/BrowPinchRight",
    "v2/BrowLowererLeft", "v2/BrowLowererRight",
    "v2/BrowInnerUpLeft", "v2/BrowInnerUpRight", "v2/BrowInnerUp",
    "v2/BrowOuterUpLeft", "v2/BrowOuterUpRight", "v2/BrowOuterUp",
    "v2/BrowDownLeft", "v2/BrowDownRight", "v2/BrowUp",
    "v2/BrowExpressionLeft", "v2/BrowExpressionRight", "v2/BrowExpression",
    "v2/JawOpen", "v2/MouthClosed", "v2/JawX", "v2/JawZ", "v2/JawClench",
    "v2/MouthCornerPullLeft", "v2/MouthCornerPullRight",
    "v2/MouthFrownLeft", "v2/MouthFrownRight",
    "v2/MouthSmileLeft", "v2/MouthSmileRight", "v2/MouthSmile",
    "v2/MouthSadLeft", "v2/MouthSadRight", "v2/MouthSad",
    "v2/MouthX", "v2/MouthUpperX", "v2/MouthLowerX",
    "v2/MouthFunnel", "v2/MouthPucker",
    "v2/MouthStretchLeft", "v2/MouthStretchRight",
    "v2/MouthPressLeft", "v2/MouthPressRight",
    "v2/MouthLowerDownLeft", "v2/MouthLowerDownRight",
    "v2/MouthUpperUpLeft", "v2/MouthUpperUpRight",
    "v2/MouthDimpleLeft", "v2/MouthDimpleRight",
    "v2/TongueOut", "v2/TongueUp", "v2/TongueDown", "v2/TongueLeft", "v2/TongueRight",
    "v2/CheekPuffLeft", "v2/CheekPuffRight", "v2/CheekPuff",
    "v2/CheekSuckLeft", "v2/CheekSuckRight",
    "v2/CheekSquintLeft", "v2/CheekSquintRight",
    "v2/NoseSneerLeft", "v2/NoseSneerRight", "v2/NoseSneer",
    "v2/NeckFlexLeft", "v2/NeckFlexRight",
]

RANGES = {
    "FaceAngleX": (-30, 30), "FaceAngleY": (-30, 30), "FaceAngleZ": (-30, 30),
    "FacePositionX": (-1, 1), "FacePositionY": (-1, 1), "FacePositionZ": (-1, 1),
    "EyeLeftX": (-1, 1), "EyeLeftY": (-1, 1), "EyeRightX": (-1, 1), "EyeRightY": (-1, 1),
    "EyeOpenLeft": (0, 1), "EyeOpenRight": (0, 1),
    "MouthOpen": (0, 1), "VoiceVolumePlusMouthOpen": (0, 1),
    "MouthSmile": (0, 1), "MouthX": (-1, 1),
    "Brows": (-1, 1), "BrowLeftY": (-1, 1), "BrowRightY": (-1, 1),
    "CheekPuff": (0, 1), "TongueOut": (0, 1), "FaceAngry": (0, 1),
}

ALIASES = {
    "paramanglex": "FaceAngleX", "paramangley": "FaceAngleY", "paramanglez": "FaceAngleZ",
    "parambodyanglex": "FaceAngleX", "parambodyangley": "FaceAngleY", "parambodyanglez": "FaceAngleZ",
    "parammouthopeny": "MouthOpen", "parammouthform": "MouthSmile",
    "mouthopeny": "MouthOpen", "voicevolumeplusmouthopen": "MouthOpen",
    "parammouthx": "MouthX",
    "parameyeballx": "EyeLeftX", "parameyebally": "EyeLeftY",
    "parameyeopenl": "EyeOpenLeft", "parameyeopenr": "EyeOpenRight",
    "parambrowly": "BrowLeftY", "parambrowry": "BrowRightY",
    "paramcheek": "CheekPuff",
}

raw = {}
outputs = {name: 0.0 for name in RANGES}
outputs["EyeOpenLeft"] = 1.0
outputs["EyeOpenRight"] = 1.0
smooth = dict(outputs)
blink_hold = {"EyeOpenLeft": 0, "EyeOpenRight": 0}
blink_seen = {"EyeOpenLeft": None, "EyeOpenRight": None}
packets = 0
seen = set()
model_params = {}
vd_map = None
vd_layout = None
vr_system = None
head_neutral = None
head_neutral_pos = None
head_error = None
stop_event = threading.Event()
status = {
    "vts": "Waiting for VTube Studio",
    "tracking": "Waiting for VRCFaceTracking",
    "headset": "Waiting for SteamVR",
    "detail": "Press Start, then look straight ahead.",
}


def recenter():
    global head_neutral, head_neutral_pos
    head_neutral = None
    head_neutral_pos = None
    status["detail"] = "Look straight ahead. That pose is the new neck center."
    print("Neck center cleared. Look forward.", flush=True)


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def g(name, default=0.0):
    return raw.get(name, default)


def avg(*names):
    vals = [raw[n] for n in names if n in raw]
    return sum(vals) / len(vals) if vals else 0.0


def lid_to_open(lid, squint):
    relaxed = EYE_REST
    if lid <= 0.75:
        opened = (lid / 0.75) * relaxed
    else:
        opened = relaxed + ((lid - 0.75) / 0.25) * (1.0 - relaxed)
    return clamp(opened - squint * 0.25, 0.0, 1.0)


def deadzone(v, band=0.15):
    v = clamp(v, -1, 1)
    if abs(v) <= band:
        return 0.0
    sign = 1 if v > 0 else -1
    return clamp(sign * (abs(v) - band) / (1 - band), -1, 1)


def compose():
    lx = g("EyeLeftX", g("EyeX"))
    ly = g("EyeLeftY", g("EyeY"))
    rx = g("EyeRightX", g("EyeX", lx))
    ry = g("EyeRightY", g("EyeY", ly))
    outputs["EyeLeftX"] = clamp(lx * EYE_GAZE, -1, 1)
    outputs["EyeLeftY"] = clamp(ly * EYE_GAZE, -1, 1)
    outputs["EyeRightX"] = clamp(rx * EYE_GAZE, -1, 1)
    outputs["EyeRightY"] = clamp(ry * EYE_GAZE, -1, 1)

    if "EyeLidLeft" in raw or "EyeLid" in raw:
        outputs["EyeOpenLeft"] = lid_to_open(g("EyeLidLeft", g("EyeLid", 0.75)), avg("EyeSquintLeft", "EyeSquint"))
    if "EyeLidRight" in raw or "EyeLid" in raw:
        outputs["EyeOpenRight"] = lid_to_open(g("EyeLidRight", g("EyeLid", 0.75)), avg("EyeSquintRight", "EyeSquint"))

    jaw = clamp(max(g("JawOpen"), avg("MouthLowerDownLeft", "MouthLowerDownRight")) * MOUTH_GAIN, 0, 1)
    outputs["MouthOpen"] = jaw
    outputs["VoiceVolumePlusMouthOpen"] = jaw

    smile = avg("MouthSmile", "MouthSmileLeft", "MouthSmileRight", "MouthCornerPullLeft", "MouthCornerPullRight", "MouthDimpleLeft", "MouthDimpleRight")
    sad = avg("MouthSad", "MouthSadLeft", "MouthSadRight", "MouthFrownLeft", "MouthFrownRight")
    outputs["MouthSmile"] = clamp(SMILE_REST + (smile * (1.0 - 0.5 * g("MouthPucker")) - sad) * 0.5, 0, 1)
    outputs["MouthX"] = clamp(g("MouthX", g("JawX", avg("MouthUpperX", "MouthLowerX"))), -1, 1)

    left_brow = g("BrowExpressionLeft")
    if "BrowExpressionLeft" not in raw:
        left_brow = avg("BrowInnerUpLeft", "BrowOuterUpLeft", "BrowUp") - avg("BrowLowererLeft", "BrowDownLeft", "BrowPinchLeft")
    right_brow = g("BrowExpressionRight")
    if "BrowExpressionRight" not in raw:
        right_brow = avg("BrowInnerUpRight", "BrowOuterUpRight", "BrowUp") - avg("BrowLowererRight", "BrowDownRight", "BrowPinchRight")
    if "BrowExpression" in raw and "BrowExpressionLeft" not in raw:
        left_brow = right_brow = g("BrowExpression")
    outputs["BrowLeftY"] = clamp(deadzone(left_brow) * BROW_GAIN, -1, 1)
    outputs["BrowRightY"] = clamp(deadzone(right_brow) * BROW_GAIN, -1, 1)
    outputs["Brows"] = clamp(deadzone((left_brow + right_brow) / 2) * BROW_GAIN, -1, 1)

    puff = g("CheekPuff", avg("CheekPuffLeft", "CheekPuffRight"))
    suck = avg("CheekSuckLeft", "CheekSuckRight")
    outputs["CheekPuff"] = clamp(puff - suck, 0, 1)
    outputs["TongueOut"] = clamp(max(0.0, g("TongueOut")), 0, 1)
    outputs["FaceAngry"] = deadzone(clamp(
        avg("BrowDownLeft", "BrowDownRight", "BrowLowererLeft", "BrowLowererRight", "NoseSneerLeft", "NoseSneerRight", "NoseSneer") * 0.6
        + sad * 0.4,
        0, 1,
    ), 0.12)
    for key in ("EyeOpenLeft", "EyeOpenRight"):
        goal = outputs[key]
        if blink_seen[key] is None:
            blink_seen[key] = goal
        elif blink_hold[key] > 0:
            blink_hold[key] -= 1
            outputs[key] = 0.0
        elif goal < blink_seen[key] - 0.12 or goal < 0.18:
            outputs[key] = 0.0
            blink_hold[key] = 4
        blink_seen[key] = goal


def smooth_outputs():
    for key, goal in outputs.items():
        if key.startswith("EyeOpen"):
            smooth[key] = goal
            continue
        a = 0.35 if key.startswith("Face") else 0.55
        smooth[key] += (goal - smooth[key]) * a


def map_range(value, src_lo, src_hi, dst_lo, dst_hi):
    if dst_lo is None or dst_hi is None or dst_lo == dst_hi:
        dst_lo, dst_hi = src_lo, src_hi
    if src_hi == src_lo:
        return dst_lo
    t = clamp((value - src_lo) / (src_hi - src_lo), 0.0, 1.0)
    return dst_lo + t * (dst_hi - dst_lo)


def source_for(name):
    if name in RANGES:
        return name
    key = "".join(ch for ch in name.lower() if ch.isalnum())
    if key in ALIASES:
        return ALIASES[key]
    if "mouthopen" in key or key.endswith("openy"):
        return "MouthOpen"
    if "mouthform" in key or "mouthsmile" in key:
        return "MouthSmile"
    return None


def parameter_payload():
    payload = []
    items = list(model_params.items())
    if not items:
        items = [(name, {"min": lo, "max": hi}) for name, (lo, hi) in RANGES.items()]
    seen_ids = set()
    for name, info in items:
        source = source_for(name)
        if source not in RANGES:
            continue
        lo, hi = RANGES[source]
        payload.append({
            "id": name,
            "weight": 1,
            "value": map_range(smooth[source], lo, hi, info.get("min"), info.get("max")),
        })
        seen_ids.add(name)
    for name, (lo, hi) in RANGES.items():
        if name in seen_ids:
            continue
        info = model_params.get(name, {})
        payload.append({
            "id": name,
            "weight": 1,
            "value": map_range(smooth[name], lo, hi, info.get("min"), info.get("max")),
        })
    return payload


def poll_head():
    global vr_system, head_neutral, head_neutral_pos, head_error
    if head_error == "missing":
        return
    try:
        import openvr
    except ImportError:
        if head_error is None:
            head_error = "missing"
            status["headset"] = "Waiting for SteamVR"
            print("Neck needs SteamVR. Run: pip install openvr", flush=True)
        return
    try:
        if vr_system is None:
            openvr.init(openvr.VRApplication_Background)
            vr_system = openvr.VRSystem()
            status["headset"] = "Connected"
            print("Headset connected. Look forward, this pose is the neck center.", flush=True)
        poses = vr_system.getDeviceToAbsoluteTrackingPose(
            openvr.TrackingUniverseStanding, 0, openvr.k_unMaxTrackedDeviceCount
        )
        pose = poses[openvr.k_unTrackedDeviceIndex_Hmd]
        if not pose.bPoseIsValid:
            status["headset"] = "Waiting for SteamVR"
            return
        status["headset"] = "Connected"
        m = pose.mDeviceToAbsoluteTracking
        rot = tuple(tuple(m[r][c] for c in range(3)) for r in range(3))
        pos = (m[0][3], m[1][3], m[2][3])
        if head_neutral is None:
            head_neutral = rot
            head_neutral_pos = pos
            return
        rel = _mul(_transpose(head_neutral), rot)
        yaw, pitch, roll = _euler(rel)
        if INVERT_YAW:
            yaw = -yaw
        if INVERT_PITCH:
            pitch = -pitch
        if INVERT_ROLL:
            roll = -roll
        flex = g("NeckFlexRight") - g("NeckFlexLeft")
        outputs["FaceAngleX"] = clamp((yaw + flex * 8.0) * HEAD_GAIN, -30, 30)
        outputs["FaceAngleY"] = clamp(pitch * HEAD_GAIN, -30, 30)
        outputs["FaceAngleZ"] = clamp(roll * HEAD_GAIN, -30, 30)
        dx = (pos[0] - head_neutral_pos[0]) / 0.30
        dy = (pos[1] - head_neutral_pos[1]) / 0.22
        dz = (pos[2] - head_neutral_pos[2]) / 0.30
        outputs["FacePositionX"] = clamp(dx, -1, 1)
        outputs["FacePositionY"] = clamp(dy, -1, 1)
        outputs["FacePositionZ"] = clamp(-dz, -1, 1)
    except Exception as exc:
        status["headset"] = "Waiting for SteamVR"
        vr_system = None
        try:
            import openvr
            openvr.shutdown()
        except Exception:
            pass
        if head_error is None:
            head_error = str(exc)
            print(f"Headset pose unavailable ({exc}). Face still runs.", flush=True)


def _transpose(a):
    return tuple(tuple(a[c][r] for c in range(3)) for r in range(3))


def _mul(a, b):
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3))
        for i in range(3)
    )


def _euler(m):
    yaw = math.degrees(math.atan2(m[0][2], m[2][2]))
    pitch = math.degrees(math.atan2(-m[1][2], math.sqrt(m[0][2] ** 2 + m[2][2] ** 2)))
    roll = math.degrees(math.atan2(m[1][0], m[1][1]))
    return yaw, pitch, roll


def on_param(addr, *args):
    global packets
    if not args:
        return
    try:
        value = float(args[0])
    except (TypeError, ValueError):
        return
    packets += 1
    name = addr.rsplit("/", 1)[-1]
    if name.endswith("Negative"):
        name = name[: -len("Negative")]
        value = -abs(value)
    elif name.endswith("Positive"):
        name = name[: -len("Positive")]
    if name not in seen:
        seen.add(name)
        print(f"tracking {name}", flush=True)
    raw[name] = value


def leaf(path):
    return {"FULL_PATH": path, "TYPE": "f", "VALUE": [0.0]}


def parameter_tree():
    root = {"FULL_PATH": "/avatar/parameters", "CONTENTS": {}}
    for name in PARAMS:
        node = root["CONTENTS"]
        built = "/avatar/parameters"
        parts = name.split("/")
        for index, part in enumerate(parts):
            built += "/" + part
            if index == len(parts) - 1:
                node[part] = leaf(built)
            else:
                node = node.setdefault(part, {"FULL_PATH": built, "CONTENTS": {}})["CONTENTS"]
    return root


AVATAR = {
    "FULL_PATH": "/avatar",
    "CONTENTS": {
        "change": {"FULL_PATH": "/avatar/change", "TYPE": "s", "VALUE": ["avtr_vts_bridge"]},
        "parameters": parameter_tree(),
    },
}
HOST_INFO = {
    "NAME": "VRChat-Client-VTS",
    "EXTENSIONS": {"ACCESS": True, "CLIPMODE": False, "RANGE": True, "TYPE": True, "VALUE": True},
    "OSC_IP": OSC_IP,
    "OSC_PORT": OSC_PORT,
    "OSC_TRANSPORT": "UDP",
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/avatar", "/avatar/parameters"):
            self._send(AVATAR if path != "/" else {"FULL_PATH": "/", "CONTENTS": {"avatar": AVATAR}})
            print(f"VRCFaceTracking read {path}", flush=True)
            return
        if "HOST_INFO" in self.path:
            self._send(HOST_INFO)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt, *args):
        return


def advertise():
    info = ServiceInfo(
        "_oscjson._tcp.local.",
        "VRChat-Client-VTS._oscjson._tcp.local.",
        addresses=[OSC_IP],
        port=HTTP_PORT,
        properties={},
        server="VRChat-Client-VTS.local.",
    )
    zc = Zeroconf(ip_version=IPVersion.V4Only)
    zc.register_service(info)
    print(f"Advertising VRChat-Client-VTS, {len(PARAMS)} face shapes.", flush=True)
    return zc


def open_vd_map():
    global vd_map
    if sys.platform != "win32":
        return
    try:
        vd_map = mmap.mmap(-1, 4096, tagname="VirtualDesktop.BodyState", access=mmap.ACCESS_READ)
    except (OSError, ValueError):
        vd_map = None


def read_vd_fallback():
    global vd_layout
    if vd_map is None or packets:
        return
    buf = vd_map[:]
    if vd_layout is None:
        def ok(off):
            vals = struct.unpack_from("<70f", buf, off)
            return sum(-0.25 <= v <= 1.25 and math.isfinite(v) for v in vals) >= 55
        if ok(4):
            vd_layout = (4, 296, 292)
        elif ok(2):
            vd_layout = (2, 292, 290)
        else:
            return
    weight_off, pose_off, valid_off = vd_layout
    weights = struct.unpack_from("<70f", buf, weight_off)
    raw["JawOpen"] = weights[24]
    raw["EyeLidLeft"] = (1.0 - weights[12]) * 0.75
    raw["EyeLidRight"] = (1.0 - weights[13]) * 0.75
    raw["MouthCornerPullLeft"] = weights[32]
    raw["MouthCornerPullRight"] = weights[33]
    if buf[valid_off] or buf[valid_off + 1]:
        def gaze(off):
            x, y, z, w = struct.unpack_from("<4f", buf, off)
            mag = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
            x, y, z, w = x / mag, y / mag, z / mag, w / mag
            pitch = math.asin(max(-1.0, min(1.0, 2 * (x * z - w * y))))
            yaw = math.atan2(2 * (y * z + w * x), w * w - x * x - y * y + z * z)
            return pitch * 1.6, yaw * 1.6
        raw["EyeLeftX"], raw["EyeLeftY"] = gaze(pose_off)
        raw["EyeRightX"], raw["EyeRightY"] = gaze(pose_off + 28)


async def vts_session():
    global model_params
    token = None
    if os.path.exists(TOKEN_PATH):
        token = json.load(open(TOKEN_PATH, encoding="utf-8")).get("token")
    next_params = 0.0

    while not stop_event.is_set():
        try:
            async with websockets.connect(VTS_URL) as ws:
                async def call(message_type, data, request_id):
                    await ws.send(json.dumps({
                        "apiName": "VTubeStudioPublicAPI",
                        "apiVersion": "1.0",
                        "requestID": request_id,
                        "messageType": message_type,
                        "data": data,
                    }))
                    return json.loads(await ws.recv())

                if not token:
                    resp = await call("AuthenticationTokenRequest", {
                        "pluginName": PLUGIN_NAME,
                        "pluginDeveloper": PLUGIN_DEV,
                    }, "token")
                    token = resp["data"]["authenticationToken"]
                    json.dump({"token": token}, open(TOKEN_PATH, "w", encoding="utf-8"))
                    print("Approved in VTube Studio. Token saved.", flush=True)

                auth = await call("AuthenticationRequest", {
                    "pluginName": PLUGIN_NAME,
                    "pluginDeveloper": PLUGIN_DEV,
                    "authenticationToken": token,
                }, "auth")
                if not auth.get("data", {}).get("authenticated"):
                    token = None
                    if os.path.exists(TOKEN_PATH):
                        os.remove(TOKEN_PATH)
                    print("Token rejected. Allow the popup.", flush=True)
                    status["vts"] = "Waiting for VTube Studio"
                    status["detail"] = "Allow the popup in VTube Studio."
                    await asyncio.sleep(2)
                    continue

                print("Connected to VTube Studio.", flush=True)
                status["vts"] = "Connected"
                while not stop_event.is_set():
                    now = time.monotonic()
                    if now >= next_params:
                        next_params = now + 5
                        listed = await call("InputParameterListRequest", {}, "list")
                        data = listed.get("data") or {}
                        found = {}
                        for entry in data.get("defaultParameters", []) + data.get("customParameters", []):
                            found[entry["name"]] = entry
                        if found:
                            model_params = found
                            print(f"Model inputs: {len(model_params)}", flush=True)
                    read_vd_fallback()
                    compose()
                    smooth_outputs()
                    values = parameter_payload()
                    if values:
                        await call("InjectParameterDataRequest", {
                            "faceFound": True,
                            "mode": "set",
                            "parameterValues": values,
                        }, "inject")
                    await asyncio.sleep(0.033)
        except Exception as exc:
            if stop_event.is_set():
                return
            print(f"VTube Studio not ready ({exc}). Retrying.", flush=True)
            status["vts"] = "Waiting for VTube Studio"
            await asyncio.sleep(2)


async def neck_loop():
    while not stop_event.is_set():
        poll_head()
        await asyncio.sleep(0.033 if status["headset"] == "Connected" else 0.4)


async def run():
    dispatcher = Dispatcher()
    dispatcher.set_default_handler(on_param)
    server = AsyncIOOSCUDPServer((OSC_IP, OSC_PORT), dispatcher, asyncio.get_running_loop())
    transport, _ = await server.create_serve_endpoint()
    print(f"Listening on {OSC_IP}:{OSC_PORT}", flush=True)
    print("Look forward. Quit VRCFaceTracking and open it again so it loads the new shapes.", flush=True)
    status["detail"] = "Look forward. Quit VRCFaceTracking and open it again."
    neck_task = asyncio.create_task(neck_loop())
    vts_task = asyncio.create_task(vts_session())
    try:
        ticks = 0
        while not stop_event.is_set():
            await asyncio.sleep(0.5)
            ticks += 1
            status["tracking"] = "Connected" if packets else "Waiting for VRCFaceTracking"
            if ticks % 4:
                continue
            print(
                f"packets={packets} eye=({smooth['EyeLeftX']:.2f},{smooth['EyeLeftY']:.2f}) "
                f"open={smooth['EyeOpenLeft']:.2f} mouth={smooth['MouthOpen']:.2f} "
                f"smile={smooth['MouthSmile']:.2f} brow={smooth['Brows']:.2f} "
                f"head=({smooth['FaceAngleX']:.1f},{smooth['FaceAngleY']:.1f},{smooth['FaceAngleZ']:.1f})",
                flush=True,
            )
    finally:
        neck_task.cancel()
        vts_task.cancel()
        transport.close()


def main():
    stop_event.clear()
    status["headset"] = "Waiting for SteamVR"
    status["tracking"] = "Waiting for VRCFaceTracking"
    status["vts"] = "Waiting for VTube Studio"
    ThreadingHTTPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer((OSC_IP, HTTP_PORT), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    open_vd_map()
    zc = advertise()
    try:
        asyncio.run(run())
    finally:
        zc.close()
        httpd.shutdown()
        status["headset"] = "Waiting for SteamVR"
        status["tracking"] = "Waiting for VRCFaceTracking"
        status["vts"] = "Waiting for VTube Studio"
        status["detail"] = "Stopped."


if __name__ == "__main__":
    main()