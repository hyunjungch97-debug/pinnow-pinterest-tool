#!/usr/bin/env python3
"""pinnow 앱 아이콘 생성 → pinnow.icns"""

import os
import math
from PIL import Image, ImageDraw

SIZE = 1024
OUT_DIR = "/Users/choi_c0re/pinnow/pinnow.iconset"
os.makedirs(OUT_DIR, exist_ok=True)


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size

    # ── 배경: 라운드 사각형, 그라디언트 시뮬레이션 (위→아래 레이어 합성)
    radius = int(s * 0.22)
    bg_rect = [0, 0, s, s]

    # 그라디언트: 위쪽 #FF2040 → 아래쪽 #CC0018
    layers = 60
    for i in range(layers):
        t = i / layers
        r = int(255 * (1 - t * 0.18))
        g = int(0)
        b = int(0 + t * 0)
        top = int(s * t / 1.5)
        btm = int(s * (t + 1 / layers) / 1.5 + s * (1 - 1 / 1.5))
        d.rectangle([0, top, s, btm], fill=(r, g, b, 255))

    # 라운드 마스크 적용
    mask = Image.new("L", (s, s), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, s, s], radius=radius, fill=255)
    img.putalpha(mask)

    # ── 핀 심볼 (원 + 꼬리)
    # 원: 상단 중앙
    cx = s * 0.5
    cy = s * 0.36

    outer_r = s * 0.225
    inner_r = s * 0.10

    # 외곽 원 (흰색)
    d.ellipse(
        [cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r],
        fill=(255, 255, 255, 255),
    )
    # 내부 구멍 (빨간색)
    hole_color = (220, 0, 20, 255)
    d.ellipse(
        [cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r],
        fill=hole_color,
    )

    # 꼬리: 아래쪽으로 뾰족한 삼각형 (원의 하단에서 시작)
    tail_top_w = s * 0.10
    tail_x1 = cx - tail_top_w
    tail_x2 = cx + tail_top_w
    tail_y1 = cy + outer_r * 0.85
    tail_tip_y = s * 0.80

    # 부드러운 꼬리: 베지어 대신 polygon
    d.polygon(
        [
            (tail_x1, tail_y1),
            (tail_x2, tail_y1),
            (cx, tail_tip_y),
        ],
        fill=(255, 255, 255, 255),
    )

    # 원과 꼬리 사이 틈 메우기
    d.rectangle(
        [tail_x1, cy + outer_r * 0.5, tail_x2, tail_y1 + 2],
        fill=(255, 255, 255, 255),
    )

    # 내부 구멍 다시 (꼬리 위에 덮어서 유지)
    d.ellipse(
        [cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r],
        fill=hole_color,
    )

    return img


# macOS iconset 규격 sizes
ICON_SIZES = [16, 32, 64, 128, 256, 512, 1024]

base = draw_icon(1024)

for sz in ICON_SIZES:
    resized = base.resize((sz, sz), Image.LANCZOS)
    resized.save(f"{OUT_DIR}/icon_{sz}x{sz}.png")
    # @2x (retina)
    if sz <= 512:
        resized2 = base.resize((sz * 2, sz * 2), Image.LANCZOS)
        resized2.save(f"{OUT_DIR}/icon_{sz}x{sz}@2x.png")

print(f"아이콘 생성 완료 → {OUT_DIR}")
