import React from 'react';
import {AbsoluteFill, Img, interpolate, Easing, staticFile, useCurrentFrame} from 'remotion';
import {C, FONT} from '../theme';
import {Rect, Shot} from '../types';

// 앱 캡처를 화면 위쪽(자막 띠 위)에 맞추고, 문장마다 지정한 곳으로 부드럽게 확대·강조
const AREA = {x: 110, y: 70, w: 1700, h: 820};
const FPS = 30;

type Cam = {s: number; tx: number; ty: number};

const camFor = (size: [number, number], zoom: Rect | null): Cam => {
  const [w, h] = size;
  const base = Math.min(AREA.w / w, AREA.h / h, 1.35);
  if (!zoom) {
    return {s: base, tx: AREA.x + (AREA.w - w * base) / 2, ty: AREA.y + (AREA.h - h * base) / 2};
  }
  const [zx, zy, zw, zh] = zoom;
  const pad = 40;
  const s = Math.min(AREA.w / (zw + pad * 2), AREA.h / (zh + pad * 2), base * 2.1);
  const cx = zx + zw / 2;
  const cy = zy + zh / 2;
  let tx = AREA.x + AREA.w / 2 - cx * s;
  let ty = AREA.y + AREA.h / 2 - cy * s;
  // 그림 밖 빈 곳이 보이지 않게
  tx = Math.min(AREA.x, Math.max(AREA.x + AREA.w - w * s, tx));
  ty = Math.min(AREA.y, Math.max(AREA.y + AREA.h - h * s, ty));
  if (w * s < AREA.w) tx = AREA.x + (AREA.w - w * s) / 2;
  if (h * s < AREA.h) ty = AREA.y + (AREA.h - h * s) / 2;
  return {s, tx, ty};
};

const mix = (a: Cam, b: Cam, p: number): Cam => ({s: a.s + (b.s - a.s) * p, tx: a.tx + (b.tx - a.tx) * p, ty: a.ty + (b.ty - a.ty) * p});

export const AppShot: React.FC<{shot: Shot}> = ({shot}) => {
  const frame = useCurrentFrame();
  const t = frame / FPS + shot.start;
  const v0 = shot.lines[0].view;
  if (v0.k !== 'app') return null;
  const size = v0.size;
  // 지금 문장과 그 문장의 확대 목표
  let idx = 0;
  shot.lines.forEach((ln, i) => {
    if (t >= ln.start) idx = i;
  });
  const cur = shot.lines[idx].view as typeof v0;
  const full = camFor(size, null);
  // 각 문장: 처음 0.5초는 전체, 이후 1초 동안 확대 (첫 문장), 다음 문장은 앞 목표에서 이어서 이동
  const targetOf = (i: number) => camFor(size, (shot.lines[i].view as typeof v0).zoom);
  const prev = idx === 0 ? full : targetOf(idx - 1);
  const lnStart = shot.lines[idx].start;
  const delay = idx === 0 ? 0.6 : 0.1;
  const p = interpolate(t - lnStart, [delay, delay + 1.1], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.cubic),
  });
  const cam = mix(prev, targetOf(idx), p);
  // 아주 느린 밀어 들어가기 (정지 화면 느낌 줄이기)
  const drift = 1 + 0.012 * (frame / (FPS * 10));
  const hlOpacity = interpolate(t - lnStart, [delay + 1.0, delay + 1.4], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const pulse = 0.65 + 0.35 * Math.sin((frame / FPS) * Math.PI * 1.6);
  const tag = cur.tag;
  return (
    <AbsoluteFill style={{overflow: 'hidden'}}>
      {/* 확대해도 화면 틀(AREA) 밖으로 나가지 않게 자름 */}
      <div style={{position: 'absolute', left: AREA.x, top: AREA.y, width: AREA.w, height: AREA.h, overflow: 'hidden', borderRadius: 10}}>
      <div
        style={{
          position: 'absolute',
          left: -AREA.x,
          top: -AREA.y,
          transformOrigin: '0 0',
          transform: `translate(${cam.tx}px, ${cam.ty}px) scale(${cam.s * drift})`,
        }}
      >
        <Img
          src={staticFile(`shots/${v0.img}.png`)}
          style={{width: size[0], height: size[1], borderRadius: 6, boxShadow: '0 30px 80px rgba(0,0,0,0.55)', display: 'block'}}
        />
        {cur.hl.map((r, i) => (
          <div
            key={i}
            style={{
              position: 'absolute',
              left: r[0] - 6,
              top: r[1] - 6,
              width: r[2] + 12,
              height: r[3] + 12,
              border: `${4 / cam.s}px solid ${C.accent}`,
              borderRadius: 8,
              opacity: hlOpacity * pulse,
              boxShadow: `0 0 ${24 / cam.s}px ${C.accent}`,
            }}
          />
        ))}
      </div>
      </div>
      {tag ? (
        <div
          style={{
            position: 'absolute',
            left: AREA.x,
            top: 14,
            fontFamily: FONT,
            fontSize: 30,
            fontWeight: 700,
            color: '#0B1220',
            background: C.accent,
            padding: '8px 22px',
            borderRadius: 999,
            boxShadow: '0 8px 24px rgba(0,0,0,0.35)',
          }}
        >
          {tag}
        </div>
      ) : null}
      <div style={{position: 'absolute', right: 34, bottom: 14, fontFamily: FONT, fontSize: 20, color: C.sub}}>{cur.note}</div>
    </AbsoluteFill>
  );
};
