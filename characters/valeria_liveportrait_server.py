#!/usr/bin/env python3
"""
Valeria LivePortrait Server — Real-time photorealistic avatar with emotion control.

Architecture:
  Browser <-> WebSocket (this server, port 8791)
    - Client sends: {"emotion": "happy"} or {"emotion": "neutral"}
    - Server responds: base64-encoded JPEG frames at ~20fps

  Valeria middleware (port 8790) handles LLM chat separately.
  The browser parses [emotion_tag] from LLM responses and forwards to this server.

Emotion -> Expression Parameter Mapping:
  LivePortrait exposes: smile, eyebrow, wink, blink, pupil_x/y, mouth shapes (aaa/eee/woo),
  head rotation (pitch/yaw/roll). We map Valeria's 14 emotion tags to these params.
"""

import asyncio
import base64
import io
import json
import logging
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch

# Add LivePortrait to path
LIVEPORTRAIT_DIR = Path(__file__).parent / "LivePortrait"
sys.path.insert(0, str(LIVEPORTRAIT_DIR))

from src.config.inference_config import InferenceConfig
from src.config.crop_config import CropConfig
from src.live_portrait_wrapper import LivePortraitWrapper
from src.utils.cropper import Cropper
from src.utils.camera import get_rotation_matrix
from src.utils.crop import prepare_paste_back, paste_back
from src.utils.io import load_img_online

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("valeria-avatar")

# ---------- Emotion -> LivePortrait Parameter Mapping ----------

EMOTION_PARAMS = {
    "neutral": {
        "smile": 0.0, "eyebrow": 0.0, "wink": 0.0,
        "pupil_x": 0.0, "pupil_y": 0.0,
        "aaa": 0.0, "eee": 0.0, "woo": 0.0,
        "pitch": 0.0, "yaw": 0.0, "roll": 0.0,
        "blink": 0.0,
    },
    "happy": {
        "smile": 0.8, "eyebrow": 3.0, "wink": 0.0,
        "pupil_x": 0.0, "pupil_y": 0.0,
        "aaa": 10.0, "eee": 5.0, "woo": 0.0,
        "pitch": -2.0, "yaw": 0.0, "roll": 2.0,
        "blink": 0.0,
    },
    "sad": {
        "smile": -0.2, "eyebrow": -6.0, "wink": 0.0,
        "pupil_x": -3.0, "pupil_y": 5.0,
        "aaa": 0.0, "eee": 0.0, "woo": 3.0,
        "pitch": 5.0, "yaw": -3.0, "roll": -2.0,
        "blink": 3.0,
    },
    "angry": {
        "smile": -0.15, "eyebrow": 10.0, "wink": 0.0,
        "pupil_x": 0.0, "pupil_y": -2.0,
        "aaa": 5.0, "eee": 3.0, "woo": 0.0,
        "pitch": -3.0, "yaw": 0.0, "roll": 0.0,
        "blink": 0.0,
    },
    "surprised": {
        "smile": 0.1, "eyebrow": 12.0, "wink": 0.0,
        "pupil_x": 0.0, "pupil_y": 0.0,
        "aaa": 40.0, "eee": 0.0, "woo": 8.0,
        "pitch": -4.0, "yaw": 0.0, "roll": 0.0,
        "blink": -5.0,
    },
    "love": {
        "smile": 0.6, "eyebrow": 2.0, "wink": 0.0,
        "pupil_x": 2.0, "pupil_y": 3.0,
        "aaa": 0.0, "eee": 3.0, "woo": 0.0,
        "pitch": 3.0, "yaw": 5.0, "roll": 3.0,
        "blink": 2.0,
    },
    "shy": {
        "smile": 0.3, "eyebrow": -2.0, "wink": 0.0,
        "pupil_x": -5.0, "pupil_y": 5.0,
        "aaa": 0.0, "eee": 0.0, "woo": 0.0,
        "pitch": 5.0, "yaw": -8.0, "roll": -3.0,
        "blink": 4.0,
    },
    "jealous": {
        "smile": -0.1, "eyebrow": 5.0, "wink": 0.0,
        "pupil_x": -5.0, "pupil_y": -2.0,
        "aaa": 0.0, "eee": 4.0, "woo": 0.0,
        "pitch": -2.0, "yaw": -5.0, "roll": 0.0,
        "blink": 0.0,
    },
    "serious": {
        "smile": -0.05, "eyebrow": 3.0, "wink": 0.0,
        "pupil_x": 0.0, "pupil_y": -1.0,
        "aaa": 0.0, "eee": 0.0, "woo": 0.0,
        "pitch": -1.0, "yaw": 0.0, "roll": 0.0,
        "blink": 0.0,
    },
    "bored": {
        "smile": 0.0, "eyebrow": -4.0, "wink": 0.0,
        "pupil_x": 5.0, "pupil_y": 3.0,
        "aaa": 0.0, "eee": 0.0, "woo": 0.0,
        "pitch": 3.0, "yaw": 8.0, "roll": -5.0,
        "blink": 5.0,
    },
    "suspicious": {
        "smile": 0.0, "eyebrow": 6.0, "wink": 5.0,
        "pupil_x": -4.0, "pupil_y": -2.0,
        "aaa": 0.0, "eee": 2.0, "woo": 0.0,
        "pitch": -2.0, "yaw": -4.0, "roll": -2.0,
        "blink": 0.0,
    },
    "victory": {
        "smile": 1.0, "eyebrow": 5.0, "wink": 8.0,
        "pupil_x": 0.0, "pupil_y": 0.0,
        "aaa": 15.0, "eee": 5.0, "woo": 0.0,
        "pitch": -5.0, "yaw": 3.0, "roll": 4.0,
        "blink": 0.0,
    },
    "sleep": {
        "smile": 0.1, "eyebrow": -5.0, "wink": 0.0,
        "pupil_x": 0.0, "pupil_y": 5.0,
        "aaa": 0.0, "eee": 0.0, "woo": 0.0,
        "pitch": 8.0, "yaw": -3.0, "roll": -5.0,
        "blink": 15.0,
    },
    "relaxed": {
        "smile": 0.3, "eyebrow": -1.0, "wink": 0.0,
        "pupil_x": 2.0, "pupil_y": 2.0,
        "aaa": 0.0, "eee": 0.0, "woo": 0.0,
        "pitch": 2.0, "yaw": 3.0, "roll": 2.0,
        "blink": 2.0,
    },
}


class ValeriaAvatar:
    """Manages LivePortrait pipeline for single-image expression editing."""

    def __init__(self, source_image_path: str):
        log.info("Initializing LivePortrait pipeline...")

        # Config
        self.inference_cfg = InferenceConfig()
        self.crop_cfg = CropConfig()

        # Load models
        self.wrapper = LivePortraitWrapper(inference_cfg=self.inference_cfg)
        self.cropper = Cropper(crop_cfg=self.crop_cfg)

        # Prepare source image (do once)
        log.info(f"Loading source image: {source_image_path}")
        img_rgb = load_img_online(source_image_path, mode='rgb', max_dim=1280, n=2)
        self.img_rgb = img_rgb

        # Crop face
        crop_info = self.cropper.crop_source_image(img_rgb, self.cropper.crop_cfg)
        self.source_lmk = crop_info['lmk_crop']
        self.crop_M_c2o = crop_info['M_c2o']
        self.mask_ori = prepare_paste_back(
            self.inference_cfg.mask_crop, crop_info['M_c2o'],
            dsize=(img_rgb.shape[1], img_rgb.shape[0])
        )

        # Extract features (do once — expensive)
        I_s = self.wrapper.prepare_source(crop_info['img_crop_256x256'])
        self.f_s = self.wrapper.extract_feature_3d(I_s)
        self.x_s_info = self.wrapper.get_kp_info(I_s)
        self.x_s = self.wrapper.transform_keypoint(self.x_s_info)

        # Store source ratios
        from src.utils.retargeting_utils import calc_eye_close_ratio, calc_lip_close_ratio
        self.source_eye_ratio = calc_eye_close_ratio(self.source_lmk[None])
        self.source_lip_ratio = calc_lip_close_ratio(self.source_lmk[None])

        # Current emotion state
        self.current_emotion = "neutral"
        self.current_params = EMOTION_PARAMS["neutral"].copy()
        self.target_params = EMOTION_PARAMS["neutral"].copy()

        log.info("LivePortrait ready!")

    @torch.no_grad()
    def render_frame(self, params: dict) -> np.ndarray:
        """Render a single frame with the given expression parameters.
        Returns: HxWx3 uint8 BGR image.
        """
        device = self.wrapper.device

        # Clone source expression
        delta_new = self.x_s_info['exp'].clone().to(device)
        scale_new = self.x_s_info['scale'].to(device)
        t_new = self.x_s_info['t'].to(device)
        x_c_s = self.x_s_info['kp'].to(device)

        # Head rotation (relative to source)
        pitch = self.x_s_info['pitch'] + params.get('pitch', 0)
        yaw = self.x_s_info['yaw'] + params.get('yaw', 0)
        roll = self.x_s_info['roll'] + params.get('roll', 0)

        R_s = get_rotation_matrix(self.x_s_info['pitch'], self.x_s_info['yaw'], self.x_s_info['roll'])
        R_d = get_rotation_matrix(pitch, yaw, roll)
        R_d_new = (R_d @ R_s.permute(0, 2, 1)) @ R_s

        # Apply expression modifications
        smile = params.get('smile', 0)
        eyebrow = params.get('eyebrow', 0)
        wink = params.get('wink', 0)
        pupil_x = params.get('pupil_x', 0)
        pupil_y = params.get('pupil_y', 0)
        aaa = params.get('aaa', 0)
        eee = params.get('eee', 0)
        woo = params.get('woo', 0)

        # Smile — affects multiple keypoints
        if smile != 0:
            delta_new[0, 20, 1] += smile * -0.01
            delta_new[0, 14, 1] += smile * -0.02
            delta_new[0, 17, 1] += smile * 0.0065
            delta_new[0, 17, 2] += smile * 0.003
            delta_new[0, 13, 1] += smile * -0.00275
            delta_new[0, 16, 2] += smile * -0.00275
            delta_new[0, 3, 1] += smile * -0.0035
            delta_new[0, 7, 1] += smile * -0.0035

        # Eyebrow
        if eyebrow != 0:
            delta_new[0, 1, 1] += eyebrow * 0.001
            delta_new[0, 2, 1] += eyebrow * -0.001
            delta_new[0, 1, 0] += eyebrow * -0.0003
            delta_new[0, 2, 0] += eyebrow * 0.0003

        # Wink (right eye)
        if wink != 0:
            delta_new[0, 11, 1] += wink * 0.001
            delta_new[0, 13, 1] += wink * -0.0003
            delta_new[0, 17, 0] += wink * 0.0003
            delta_new[0, 17, 1] += wink * 0.0003
            delta_new[0, 3, 1] += wink * -0.0003

        # Eyeball direction
        if pupil_x != 0:
            if pupil_x > 0:
                delta_new[0, 11, 0] += pupil_x * 0.0007
                delta_new[0, 15, 0] += pupil_x * 0.001
            else:
                delta_new[0, 11, 0] += pupil_x * 0.001
                delta_new[0, 15, 0] += pupil_x * 0.0007

        if pupil_y != 0:
            delta_new[0, 11, 1] += pupil_y * -0.001
            delta_new[0, 15, 1] += pupil_y * -0.001

        # Mouth shapes
        if aaa != 0:
            delta_new[0, 19, 1] += aaa * 0.001
            delta_new[0, 19, 2] += aaa * 0.0001
        if eee != 0:
            delta_new[0, 20, 2] += eee * -0.001
            delta_new[0, 20, 1] += eee * -0.001
        if woo != 0:
            delta_new[0, 19, 1] += woo * 0.001
            delta_new[0, 19, 2] += woo * -0.001

        # Compose final keypoints
        x_d_new = scale_new * (x_c_s @ R_d_new + delta_new) + t_new

        # Eye/lip retargeting for blink
        blink_val = params.get('blink', 0)
        if blink_val != 0:
            eye_ratio = self.source_eye_ratio + blink_val * 0.01
            combined_eye = self.wrapper.calc_combined_eye_ratio(
                [[float(eye_ratio[0][0])]],
                self.source_lmk
            )
            eyes_delta = self.wrapper.retarget_eye(self.x_s.to(device), combined_eye)
            x_d_new = x_d_new + eyes_delta

        # Stitching for smooth boundaries
        x_d_new = self.wrapper.stitching(self.x_s.to(device), x_d_new)

        # Decode
        out = self.wrapper.warp_decode(self.f_s.to(device), self.x_s.to(device), x_d_new)
        out_img = self.wrapper.parse_output(out['out'])[0]

        # Paste back onto original image
        result = paste_back(out_img, self.crop_M_c2o, self.img_rgb, self.mask_ori)

        return result

    def set_emotion(self, emotion: str):
        """Set target emotion — interpolation happens in animation loop."""
        emotion = emotion.lower().strip()
        if emotion in EMOTION_PARAMS:
            self.current_emotion = emotion
            self.target_params = EMOTION_PARAMS[emotion].copy()
        else:
            log.warning(f"Unknown emotion: {emotion}, defaulting to neutral")
            self.target_params = EMOTION_PARAMS["neutral"].copy()

    def interpolate_params(self, lerp_factor: float = 0.15):
        """Smoothly interpolate current params toward target."""
        for key in self.current_params:
            self.current_params[key] += (self.target_params[key] - self.current_params[key]) * lerp_factor


# ---------- WebSocket Server ----------

async def handle_websocket(reader, writer, avatar: ValeriaAvatar):
    """Handle a single WebSocket connection (raw TCP, not HTTP upgrade).
    We use aiohttp for proper WebSocket support.
    """
    pass  # Placeholder — actual server uses aiohttp below


def create_app(avatar: ValeriaAvatar):
    """Create aiohttp app with WebSocket + REST endpoints."""
    from aiohttp import web

    routes = web.RouteTableDef()

    @routes.get('/ws')
    async def ws_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        log.info("WebSocket client connected")

        # Animation loop — send frames continuously
        try:
            # Start with neutral
            avatar.set_emotion("neutral")

            # Listen for emotion changes AND send frames concurrently
            async def receive_emotions():
                async for msg in ws:
                    if msg.type == web.WSMsgType.TEXT:
                        try:
                            data = json.loads(msg.data)
                            if 'emotion' in data:
                                avatar.set_emotion(data['emotion'])
                                log.info(f"Emotion -> {data['emotion']}")
                        except json.JSONDecodeError:
                            pass
                    elif msg.type == web.WSMsgType.ERROR:
                        break

            async def send_frames():
                frame_interval = 1.0 / 15  # 15 FPS to start (can increase)
                while not ws.closed:
                    t0 = time.time()

                    # Interpolate toward target emotion
                    avatar.interpolate_params(0.15)

                    # Render frame
                    frame = avatar.render_frame(avatar.current_params)

                    # Encode as JPEG
                    _, buffer = cv2.imencode('.jpg', cv2.cvtColor(frame, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 80])
                    b64 = base64.b64encode(buffer).decode('ascii')

                    # Send
                    try:
                        await ws.send_str(json.dumps({
                            "type": "frame",
                            "data": b64,
                            "emotion": avatar.current_emotion,
                            "fps": round(1.0 / max(time.time() - t0, 0.001), 1)
                        }))
                    except Exception:
                        break

                    # Frame timing
                    elapsed = time.time() - t0
                    sleep_time = max(0, frame_interval - elapsed)
                    await asyncio.sleep(sleep_time)

            # Run both concurrently
            await asyncio.gather(
                receive_emotions(),
                send_frames(),
                return_exceptions=True
            )

        except Exception as e:
            log.error(f"WebSocket error: {e}")
        finally:
            log.info("WebSocket client disconnected")

        return ws

    @routes.get('/api/emotions')
    async def list_emotions(request):
        return web.json_response(list(EMOTION_PARAMS.keys()))

    @routes.post('/api/emotion')
    async def set_emotion(request):
        data = await request.json()
        emotion = data.get('emotion', 'neutral')
        avatar.set_emotion(emotion)
        return web.json_response({"status": "ok", "emotion": emotion})

    @routes.get('/api/frame')
    async def get_frame(request):
        """Single frame as JPEG — for testing."""
        emotion = request.query.get('emotion', None)
        if emotion:
            avatar.set_emotion(emotion)
            avatar.current_params = EMOTION_PARAMS.get(emotion, EMOTION_PARAMS["neutral"]).copy()

        frame = avatar.render_frame(avatar.current_params)
        _, buffer = cv2.imencode('.jpg', cv2.cvtColor(frame, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 90])
        return web.Response(body=buffer.tobytes(), content_type='image/jpeg')

    @routes.get('/health')
    async def health(request):
        return web.json_response({"status": "alive", "emotion": avatar.current_emotion})

    @routes.post('/api/shutdown')
    async def shutdown(request):
        """Kill server and free GPU memory."""
        log.info("Shutdown requested — freeing GPU memory and exiting")
        # Free CUDA memory
        try:
            del avatar.wrapper
            del avatar.source_info
            torch.cuda.empty_cache()
            log.info("GPU memory freed")
        except Exception as e:
            log.warning(f"Cleanup error: {e}")
        # Schedule server stop
        asyncio.get_event_loop().call_later(0.5, lambda: os._exit(0))
        return web.json_response({"status": "shutting_down"})

    app = web.Application()
    app.add_routes(routes)

    # CORS middleware
    @web.middleware
    async def cors_middleware(request, handler):
        if request.method == "OPTIONS":
            resp = web.Response()
        else:
            resp = await handler(request)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    app.middlewares.append(cors_middleware)

    return app


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Valeria LivePortrait Avatar Server")
    parser.add_argument("--source", type=str, required=True, help="Path to source image (Valeria photo)")
    parser.add_argument("--port", type=int, default=8791, help="Server port")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Server host")
    args = parser.parse_args()

    from aiohttp import web

    # Initialize avatar
    avatar = ValeriaAvatar(args.source)

    # Create and run app
    app = create_app(avatar)
    log.info(f"Starting Valeria Avatar server on {args.host}:{args.port}")
    web.run_app(app, host=args.host, port=args.port, print=log.info)


if __name__ == "__main__":
    main()
