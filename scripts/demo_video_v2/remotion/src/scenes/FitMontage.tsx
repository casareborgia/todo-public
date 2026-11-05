import React from 'react';
import {AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame, spring, useVideoConfig} from 'remotion';
import {C, FONT, FAKE} from '../theme';

// 창 크기 3가지: 같은 업무를 칸 높이에 맞춰 보여 줌 (캡처 픽셀 크기 비율 그대로 나란히)
const SHOTS = [
  {img: 'v2_fit_min', w: 1327, h: 882, label: '작은 창'},
  {img: 'v2_fit_def', w: 1602, h: 1057, label: '기본 창'},
  {img: 'v2_fit_max', w: 1922, h: 1072, label: '최대화'},
];

export const FitMontage: React.FC<{dur: number}> = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const k = 0.3;
  let x = 90;
  return (
    <AbsoluteFill style={{background: C.bg, fontFamily: FONT}}>
      {SHOTS.map((s, i) => {
        const p = spring({frame: frame - i * 8, fps, config: {damping: 200}});
        const w = s.w * k * 1.12;
        const h = s.h * k * 1.12;
        const left = x;
        x += w + 40;
        return (
          <div key={s.img} style={{position: 'absolute', left, top: 760 - h - 120, opacity: p, transform: `translateY(${(1 - p) * 40}px)`}}>
            <Img src={staticFile(`shots/${s.img}.png`)} style={{width: w, height: h, borderRadius: 6, boxShadow: '0 20px 50px rgba(0,0,0,0.5)'}} />
            <div style={{color: C.ink, fontSize: 30, marginTop: 16, textAlign: 'center'}}>{s.label}</div>
          </div>
        );
      })}
      <div style={{position: 'absolute', left: 0, right: 0, top: 120, textAlign: 'center', color: C.ink, fontSize: 46, fontWeight: 800, opacity: interpolate(frame, [0, 10], [0, 1])}}>
        창 크기에 맞춰 보이는 업무 수를 조절
      </div>
      <div style={{position: 'absolute', right: 34, bottom: 14, fontSize: 20, color: C.sub}}>{FAKE}</div>
    </AbsoluteFill>
  );
};
