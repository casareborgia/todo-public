import React from 'react';
import {AbsoluteFill, Easing, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {C, FONT, FAKE} from '../theme';
import {Shot} from '../types';

// 2D 모션그래픽 장면 (예전 Three.js 장면을 대신함). 앱 화면과 같은 평면 카드·선으로만 그림.
// 움직임은 프레임 번호로만 정해지므로 같은 프레임이면 늘 같은 그림이 나옵니다.

type Pt = [number, number];
const CL = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const ramp = (frame: number, a: number, b: number, ease = Easing.inOut(Easing.cubic)) => interpolate(frame, [a, b], [0, 1], {...CL, easing: ease});

const bez = (p0: Pt, p1: Pt, p2: Pt, p3: Pt, t: number): Pt => {
  const u = 1 - t;
  return [
    u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
    u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
  ];
};
const bezPath = (p0: Pt, p1: Pt, p2: Pt, p3: Pt) => `M ${p0[0]} ${p0[1]} C ${p1[0]} ${p1[1]}, ${p2[0]} ${p2[1]}, ${p3[0]} ${p3[1]}`;

const BG: React.FC<{children: React.ReactNode}> = ({children}) => (
  <AbsoluteFill style={{background: `radial-gradient(1400px 900px at 50% 35%, #15233F, ${C.bg} 70%)`, fontFamily: FONT, color: C.ink}}>{children}</AbsoluteFill>
);

const Note: React.FC = () => <div style={{position: 'absolute', right: 34, bottom: 14, fontFamily: FONT, fontSize: 20, color: C.sub}}>{FAKE}</div>;

const Pill: React.FC<{color: string; children: React.ReactNode; size?: number; solid?: boolean; style?: React.CSSProperties}> = ({color, children, size = 22, solid, style}) => (
  <span
    style={{
      display: 'inline-block',
      fontSize: size,
      fontWeight: 800,
      color: solid ? C.bg : color,
      background: solid ? color : 'transparent',
      border: `2px solid ${color}`,
      borderRadius: 999,
      padding: `${Math.round(size * 0.12)}px ${Math.round(size * 0.6)}px`,
      whiteSpace: 'nowrap',
      lineHeight: 1.25,
      ...style,
    }}
  >
    {children}
  </span>
);

// 공통 2D 카드: 왼쪽 색 띠 + 출처 태그 + 제목 + 보조 글
const FlatCard: React.FC<{
  tag: string;
  color: string;
  title: string;
  sub?: string;
  foot?: string;
  w: number;
  glow?: number;
  style?: React.CSSProperties;
}> = ({tag, color, title, sub, foot, w, glow = 0, style}) => (
  <div
    style={{
      width: w,
      boxSizing: 'border-box',
      position: 'relative',
      overflow: 'hidden',
      background: '#131E36',
      border: `1.5px solid ${glow > 0 ? color : C.line}`,
      borderRadius: 16,
      padding: '18px 24px 18px 34px',
      boxShadow: `0 18px 40px rgba(0,0,0,0.45), 0 0 ${36 * glow}px ${color}`,
      textAlign: 'left',
      ...style,
    }}
  >
    <div style={{position: 'absolute', left: 0, top: 0, bottom: 0, width: 9, background: color}} />
    <Pill color={color} size={20}>
      {tag}
    </Pill>
    <div style={{fontSize: 30, fontWeight: 800, marginTop: 10, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', letterSpacing: -0.5}}>{title}</div>
    {sub ? <div style={{fontSize: 22, color: C.sub, marginTop: 4, whiteSpace: 'nowrap'}}>{sub}</div> : null}
    {foot ? <div style={{fontSize: 19, color: '#64748B', marginTop: 10, whiteSpace: 'nowrap'}}>{foot}</div> : null}
  </div>
);

const GlowFilter: React.FC<{id: string; sd?: number}> = ({id, sd = 6}) => (
  <filter id={id} filterUnits="userSpaceOnUse" x={0} y={0} width={1920} height={1080}>
    <feGaussianBlur stdDeviation={sd} result="b" />
    <feMerge>
      <feMergeNode in="b" />
      <feMergeNode in="SourceGraphic" />
    </feMerge>
  </filter>
);

// ───────────────────────── 01 도입: 공문·쪽지·배부 카드가 쌓였다가 하나로 모임
const DESK = [
  {tag: '공문', color: C.portal, items: ['개인정보 보호 교육 계획 제출', '하반기 예산 집행실적 제출', '정보보안 서약서 제출', '국정감사 자료 제출']},
  {tag: '쪽지', color: C.messenger, items: ['회의실 비품 현황 회신', '교육 수요조사 부탁드립니다', '11/9까지 제출 부탁', '자료 정리 부탁드려요']},
  {tag: '배부', color: C.wm, items: ['내부통제 자가진단 시행 알림', '정보보호 교육 계획 제출', '업무담당자 지정', '2027년 주요업무 계획']},
];

const Desk: React.FC<{dur: number}> = ({dur}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const starts = [6, 20, 36];
  const gather = ramp(frame, dur - 34, dur - 8);
  return (
    <BG>
      {DESK.map((col, c) => {
        const cx = [560, 960, 1360][c];
        const x = lerp(cx, 960, gather);
        const head = spring({frame: frame - starts[c], fps, config: {damping: 200}});
        return (
          <div key={col.tag} style={{position: 'absolute', left: x - 190, top: 0, width: 380, transform: `scale(${1 - 0.12 * gather})`, transformOrigin: '50% 500px'}}>
            <div style={{position: 'absolute', top: 210, left: 0, width: 380, textAlign: 'center', opacity: head * (1 - gather), transform: `translateY(${(1 - head) * 16}px)`}}>
              <Pill color={col.color} size={36}>
                {col.tag}
              </Pill>
            </div>
            {col.items.map((t, i) => {
              const p = spring({frame: frame - (starts[c] + 4 + i * 7), fps, config: {damping: 15, mass: 0.7}});
              const top = 290 + i * 104;
              const tilt = (i % 2 ? 1 : -1) * 1.2 * (1 - gather);
              return (
                <div
                  key={t}
                  style={{
                    position: 'absolute',
                    left: 0,
                    top,
                    opacity: Math.min(1, p * 1.6),
                    transform: `translateY(${(1 - p) * -260}px) rotate(${tilt}deg)`,
                  }}
                >
                  <FlatCard tag={col.tag} color={col.color} title={t} w={380} />
                </div>
              );
            })}
          </div>
        );
      })}
    </BG>
  );
};

// ───────────────────────── 03 전체 구조: 세 갈래 → 업무인지 판단 → 캘린더
const SRC = [
  {tag: '공문', title: '감시 폴더 · 공문 파일', sub: '공문 PDF 저장', color: C.portal, y: 300},
  {tag: '쪽지', title: '메신저 쪽지', sub: '쪽지 창 열기', color: C.messenger, y: 500},
  {tag: '배부', title: '업무관리시스템 배부', sub: '배부 문서 목록', color: C.wm, y: 700},
];
const J: Pt = [990, 500];
const J_IN: Pt = [835, 500];
const J_OUT: Pt = [1145, 500];
const CAL = {x: 1300, w: 520, h: Math.round((520 * 1057) / 1602)};
const srcPts = (y: number): [Pt, Pt, Pt, Pt] => [[575, y], [700, y], [720, 500], J_IN];
const outPts: [Pt, Pt, Pt, Pt] = [J_OUT, [1200, 500], [1240, 500], [CAL.x - 8, 500]];

const Flow: React.FC<{at: (i: number) => number}> = ({at}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const L1 = at(1);
  const L2 = at(2);
  const mention = [70, 116, 148].map((m) => Math.min(m, L1 - 28));
  const draw = mention.map((m) => ramp(frame, m, m + 24));
  const judge = spring({frame: frame - (L1 + 6), fps, config: {damping: 13, mass: 0.8}});
  const outDraw = ramp(frame, L1 + 34, L1 + 58);
  const cal = spring({frame: frame - (L1 + 50), fps, config: {damping: 200}});
  const box = ramp(frame, L2 + 4, L2 + 28);
  const pulse = 0.5 + 0.5 * Math.sin(frame / 7);
  const packets: React.ReactNode[] = [];
  SRC.forEach((s, i) => {
    if (draw[i] < 1) return;
    const born = mention[i] + 24;
    for (let k = 0; k < 3; k++) {
      const t = ((frame - born - k * 15) / 45) % 1;
      if (frame - born - k * 15 < 0) continue;
      const [x, y] = bez(...srcPts(s.y), t);
      packets.push(<circle key={`s${i}${k}`} cx={x} cy={y} r={8} fill={s.color} filter="url(#fglow)" opacity={Math.min(1, t * 6, (1 - t) * 6)} />);
    }
  });
  if (outDraw >= 1) {
    for (let k = 0; k < 3; k++) {
      const st = L1 + 58 + k * 12;
      if (frame < st) continue;
      const t = ((frame - st) / 36) % 1;
      const [x, y] = bez(...outPts, t);
      packets.push(<circle key={`o${k}`} cx={x} cy={y} r={9} fill={C.accent} filter="url(#fglow)" opacity={Math.min(1, t * 6, (1 - t) * 6)} />);
    }
  }
  return (
    <BG>
      {/* 내 PC 경계 */}
      <div
        style={{
          position: 'absolute',
          left: 110,
          top: 170,
          width: 1700,
          height: 650,
          borderRadius: 28,
          border: `3px dashed rgba(147,197,253,${0.75 * box})`,
          background: `rgba(96,165,250,${0.05 * box})`,
          transform: `scale(${0.97 + 0.03 * box})`,
        }}
      >
        <div style={{position: 'absolute', left: 26, top: -22, opacity: box, background: C.bg, padding: '0 12px', fontSize: 26, fontWeight: 800, color: '#93C5FD'}}>🖥 내 PC</div>
      </div>
      <svg width={1920} height={1080} style={{position: 'absolute', left: 0, top: 0}}>
        <defs>
          <GlowFilter id="fglow" />
        </defs>
        {SRC.map((s, i) => {
          const d = bezPath(...srcPts(s.y));
          return (
            <g key={s.tag}>
              <path d={d} stroke={s.color} strokeOpacity={0.3} strokeWidth={6} fill="none" pathLength={1} strokeDasharray={1} strokeDashoffset={1 - draw[i]} strokeLinecap="round" />
              <path d={d} stroke={s.color} strokeWidth={3} fill="none" pathLength={100} strokeDasharray="3 5" strokeDashoffset={-frame * 0.8} opacity={draw[i] >= 1 ? 0.9 : 0} filter="url(#fglow)" />
              <path d={d} stroke={s.color} strokeWidth={3} fill="none" pathLength={1} strokeDasharray={1} strokeDashoffset={1 - draw[i]} opacity={draw[i] < 1 ? 1 : 0} filter="url(#fglow)" />
            </g>
          );
        })}
        <path d={bezPath(...outPts)} stroke={C.accent} strokeOpacity={0.35} strokeWidth={7} fill="none" pathLength={1} strokeDasharray={1} strokeDashoffset={1 - outDraw} strokeLinecap="round" />
        <path d={bezPath(...outPts)} stroke={C.accent} strokeWidth={3.5} fill="none" pathLength={100} strokeDasharray="3 5" strokeDashoffset={-frame * 0.8} opacity={outDraw >= 1 ? 0.95 : 0} filter="url(#fglow)" />
        {packets}
        {/* 판단 노드 둘레의 맥박 고리 */}
        <circle cx={J[0]} cy={J[1]} r={120 + 26 * ((frame % 40) / 40)} fill="none" stroke={C.accent} strokeWidth={2} opacity={judge * 0.5 * (1 - (frame % 40) / 40)} />
      </svg>
      {/* 왼쪽: 세 갈래 출처 카드 */}
      {SRC.map((s, i) => {
        const p = spring({frame: frame - (8 + i * 8), fps, config: {damping: 200}});
        const hot = interpolate(frame, [mention[i], mention[i] + 10, mention[i] + 40], [0, 1, 0.25], CL);
        return (
          <div key={s.tag} style={{position: 'absolute', left: 150, top: s.y - 62, opacity: p, transform: `translateX(${(1 - p) * -80}px)`}}>
            <FlatCard tag={s.tag} color={s.color} title={s.title} sub={s.sub} w={425} glow={hot} />
          </div>
        );
      })}
      {/* 가운데: 업무인지 판단 배지 */}
      <div
        style={{
          position: 'absolute',
          left: J[0] - 155,
          top: J[1] - 72,
          width: 310,
          height: 144,
          boxSizing: 'border-box',
          borderRadius: 26,
          border: `3px ${judge > 0.05 ? 'solid' : 'dashed'} ${C.accent}`,
          background: judge > 0.05 ? 'linear-gradient(180deg, #2A2410, #1A1A12)' : 'transparent',
          opacity: 0.3 + 0.7 * Math.min(1, judge),
          transform: `scale(${0.85 + 0.15 * judge})`,
          boxShadow: `0 0 ${30 + 20 * pulse * judge}px rgba(251,191,36,${0.45 * judge})`,
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          alignItems: 'center',
        }}
      >
        <div style={{fontSize: 38, fontWeight: 800, color: C.accent, opacity: judge}}>업무인지 판단</div>
        <div style={{fontSize: 20, color: '#CBD5E1', marginTop: 6, opacity: judge}}>업무 · 확인 필요 · 제외</div>
      </div>
      {/* 오른쪽: 캘린더 (실제 앱 캡처) */}
      <div style={{position: 'absolute', left: CAL.x, top: 500 - CAL.h / 2, opacity: cal, transform: `translateX(${(1 - cal) * 60}px)`}}>
        <div style={{position: 'absolute', top: -58, left: 0, width: CAL.w, textAlign: 'center'}}>
          <Pill color={C.ink} size={30}>
            캘린더
          </Pill>
        </div>
        <Img src={staticFile('shots/02_main_month.png')} style={{width: CAL.w, height: CAL.h, borderRadius: 10, boxShadow: `0 24px 60px rgba(0,0,0,0.55), 0 0 0 3px ${C.accent}`}} />
      </div>
      {/* 아래: 보안 배지 */}
      <div style={{position: 'absolute', left: 0, right: 0, top: 838, textAlign: 'center', opacity: box, transform: `translateY(${(1 - box) * 14}px)`}}>
        <span style={{fontSize: 36, fontWeight: 800, color: '#CBD5E1', background: '#0F1B33', border: '2px solid #93C5FD', borderRadius: 999, padding: '10px 34px'}}>
          🔒 내 PC 안에서만 · 외부 통신 없음
        </span>
      </div>
    </BG>
  );
};

// ───────────────────────── 05 쪽지 판별: 수신된 쪽지 → 스캔 → 세 칸으로 분류
const MSGS = [
  '안녕하세요 ^^', '[알림] 결재 도착', '점심 메뉴 공지', '검토 부탁드려요',
  '시스템 점검 안내', '감사합니다!', '회의실 예약 완료', '다음 주 중 회신',
  '[자동] 메일 수신', '좋은 아침입니다', '보안 패치 알림', '자료 보내 주세요',
  '수고 많으셨어요', '[알림] 일정 변경', '주차 안내', '일정 확인 부탁',
  '생일 축하드려요', '[공지] 전산 점검', '출장 잘 다녀오세요', '의견 있으면 주세요',
];
const isReview = (i: number) => i % 4 === 3; // 5건
const SLOT = [
  {key: 'drop', label: '자동 제외 (인사·알림)', color: '#64748B', x: 170},
  {key: 'reg', label: '업무 자동 등록', color: C.messenger, x: 710},
  {key: 'review', label: '확인 필요 (보류)', color: C.accent, x: 1250},
];
const SLOT_W = 500;
const SLOT_TOP = 470;
const SCAN_Y = 360;
const CHIP = {w: 150, h: 52, gap: 14};
const MINI = {w: 140, h: 34, gx: 12, gy: 10};

const Sort: React.FC<{dur: number; at: (i: number) => number}> = ({dur, at}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const S0 = Math.max(30, at(1)) + 6;
  const step = Math.max(4, Math.min(9, (dur - S0 - 90) / 20));
  const MOVE = 30;
  const left0 = (1920 - (10 * CHIP.w + 9 * CHIP.gap)) / 2;
  let dropN = 0;
  let reviewN = 0;
  let flash = 0;
  const chips = MSGS.map((m, i) => {
    const rev = isReview(i);
    const k = rev ? Math.floor(i / 4) : i - Math.floor((i + 1) / 4);
    const slot = rev ? SLOT[2] : SLOT[0];
    const sx = left0 + (i % 10) * (CHIP.w + CHIP.gap);
    const sy = 170 + Math.floor(i / 10) * (CHIP.h + CHIP.gap);
    const tx = slot.x + 28 + (k % 3) * (MINI.w + MINI.gx);
    const ty = SLOT_TOP + 96 + Math.floor(k / 3) * (MINI.h + MINI.gy);
    const appear = spring({frame: frame - i * 2, fps, config: {damping: 200}});
    const st = S0 + i * step;
    const p = ramp(frame, st, st + MOVE);
    if (p >= 1) {
      if (rev) reviewN++;
      else dropN++;
    }
    const cross = 0.32;
    flash = Math.max(flash, 1 - Math.abs(p - cross) * 8);
    const judged = p > cross;
    const col = judged ? slot.color : C.messenger;
    const x = lerp(sx, tx, p);
    const y = lerp(sy, ty, p);
    const w = lerp(CHIP.w, MINI.w, p);
    const h = lerp(CHIP.h, MINI.h, p);
    return (
      <div
        key={i}
        style={{
          position: 'absolute',
          left: x,
          top: y,
          width: w,
          height: h,
          boxSizing: 'border-box',
          opacity: appear,
          transform: `scale(${0.9 + 0.1 * appear})`,
          background: judged ? (rev ? '#3A3112' : '#1E293B') : '#1A2742',
          border: `2px solid ${judged ? col : C.line}`,
          borderLeft: `7px solid ${col}`,
          borderRadius: 9,
          fontSize: lerp(16, 14, p),
          color: judged && !rev ? '#94A3B8' : C.ink,
          display: 'flex',
          alignItems: 'center',
          padding: '0 8px',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          boxShadow: p > 0 && p < 1 ? `0 10px 24px rgba(0,0,0,0.5)` : 'none',
          zIndex: p > 0 && p < 1 ? 5 : 1,
        }}
      >
        {m}
      </div>
    );
  });
  const done = ramp(frame, S0 + 19 * step + MOVE + 4, S0 + 19 * step + MOVE + 22);
  const scanX = ((frame * 14) % 2000) - 200;
  return (
    <BG>
      {/* 머리글: 처음엔 '수신된 쪽지', 분류가 끝나면 결과 요약 */}
      <div style={{position: 'absolute', left: 0, right: 0, top: 92, textAlign: 'center', opacity: 1 - done}}>
        <Pill color={C.messenger} size={28} solid>
          수신된 쪽지
        </Pill>
      </div>
      <div style={{position: 'absolute', left: 0, right: 0, top: 88, textAlign: 'center', opacity: done, transform: `translateY(${(1 - done) * 12}px)`, fontSize: 36, fontWeight: 800}}>
        <span style={{color: '#CBD5E1'}}>인사·알림은 자동 제외</span>
        <span style={{color: C.sub}}> · </span>
        <span style={{color: C.messenger}}>업무는 자동 등록</span>
        <span style={{color: C.sub}}> · </span>
        <span style={{color: C.accent}}>애매한 쪽지는 확인 필요</span>
      </div>
      {/* 스캔 라인 */}
      <svg width={1920} height={1080} style={{position: 'absolute', left: 0, top: 0}}>
        <defs>
          <GlowFilter id="sglow" sd={5} />
          <linearGradient id="sweep" x1="0" x2="1">
            <stop offset="0" stopColor="#38BDF8" stopOpacity="0" />
            <stop offset="0.5" stopColor="#E0F2FE" stopOpacity="1" />
            <stop offset="1" stopColor="#38BDF8" stopOpacity="0" />
          </linearGradient>
        </defs>
        <line x1={140} x2={1780} y1={SCAN_Y} y2={SCAN_Y} stroke="#38BDF8" strokeWidth={3 + 3 * flash} opacity={0.55 + 0.45 * flash} filter="url(#sglow)" />
        <rect x={scanX} y={SCAN_Y - 3} width={220} height={6} fill="url(#sweep)" opacity={0.9} />
      </svg>
      <div style={{position: 'absolute', left: 140, top: SCAN_Y - 44, fontSize: 22, fontWeight: 800, color: '#7DD3FC'}}>스마트 판별</div>
      {/* 아래: 세 칸 */}
      {SLOT.map((s) => {
        const n = s.key === 'drop' ? dropN : s.key === 'review' ? reviewN : 0;
        const isReg = s.key === 'reg';
        return (
          <div
            key={s.key}
            style={{
              position: 'absolute',
              left: s.x,
              top: SLOT_TOP,
              width: SLOT_W,
              height: 340,
              boxSizing: 'border-box',
              borderRadius: 20,
              background: '#0F1A30',
              border: `2px solid ${s.color}`,
              boxShadow: isReg ? `0 0 ${40 * done}px rgba(67,160,71,0.6)` : 'none',
            }}
          >
            <div style={{position: 'absolute', left: 24, top: 20, fontSize: 28, fontWeight: 800, color: s.key === 'drop' ? '#CBD5E1' : s.color}}>{s.label}</div>
            {!isReg ? (
              <div style={{position: 'absolute', right: 24, top: 10, fontSize: 48, fontWeight: 800, color: s.key === 'drop' ? '#CBD5E1' : s.color}}>
                {n}
                <span style={{fontSize: 24, marginLeft: 4}}>건</span>
              </div>
            ) : (
              <div style={{position: 'absolute', left: 0, right: 0, top: 110, textAlign: 'center', opacity: 0.35 + 0.65 * done}}>
                <div style={{fontSize: 84, color: C.messenger, lineHeight: 1}}>✓</div>
                <div style={{fontSize: 40, fontWeight: 800, marginTop: 14}}>
                  업무만 <span style={{color: C.messenger}}>캘린더로</span>
                </div>
              </div>
            )}
          </div>
        );
      })}
      {chips}
      <Note />
    </BG>
  );
};

// ───────────────────────── 07 같은 일 합치기: 쪽지 카드 + 배부 카드 → 업무 1건
const Merge: React.FC<{dur: number; at: (i: number) => number}> = ({dur, at}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const M = Math.min(Math.max(at(1), 50) + 10, dur - 70);
  const inL = spring({frame: frame - 6, fps, config: {damping: 200}});
  const inR = spring({frame: frame - 16, fps, config: {damping: 200}});
  const pull = spring({frame: frame - M, fps, config: {damping: 18, mass: 0.9}});
  const fuse = ramp(frame, M + 18, M + 30);
  const flash = interpolate(frame, [M + 18, M + 24, M + 44], [0, 1, 0], CL);
  const badge = spring({frame: frame - (M + 30), fps, config: {damping: 12, mass: 0.6}});
  const ask = interpolate(frame, [36, 48, M - 4, M + 6], [0, 1, 1, 0], CL);
  const W = 540;
  const lx = lerp(250, 960 - W / 2 - 18, pull);
  const rx = lerp(1130, 960 - W / 2 + 18, pull);
  const cardTop = 360;
  return (
    <BG>
      <div style={{position: 'absolute', left: 0, right: 0, top: 150, textAlign: 'center', opacity: ask}}>
        <span style={{fontSize: 40, fontWeight: 800, color: '#CBD5E1'}}>같은 일?</span>
      </div>
      <svg width={1920} height={1080} style={{position: 'absolute', left: 0, top: 0, opacity: ask}}>
        <line x1={250 + W} x2={1130} y1={cardTop + 105} y2={cardTop + 105} stroke="#CBD5E1" strokeWidth={3} strokeDasharray="10 10" strokeDashoffset={-frame} />
      </svg>
      <div style={{position: 'absolute', left: lx, top: cardTop, opacity: inL * (1 - fuse), transform: `translateY(${(1 - inL) * 30}px)`}}>
        <FlatCard tag="동료 쪽지 · 이영희" color={C.messenger} title="내부통제 자가진단" sub="11/9까지 제출 부탁드립니다" foot="보낸사람: 이영희 (가상)" w={W} />
      </div>
      <div style={{position: 'absolute', left: rx, top: cardTop, opacity: inR * (1 - fuse), transform: `translateY(${(1 - inR) * 30}px)`}}>
        <FlatCard tag="업무관리시스템 배부 · 감사실-456" color={C.wm} title="2026년 내부통제 자가진단" sub="결과 제출 시행 알림" foot="가상감사실 · 업무담당자" w={W} />
      </div>
      {/* 합쳐질 때 번지는 고리 */}
      <div
        style={{
          position: 'absolute',
          left: 960 - 300,
          top: cardTop + 105 - 300,
          width: 600,
          height: 600,
          borderRadius: '50%',
          border: `4px solid ${C.accent}`,
          opacity: flash * 0.8,
          transform: `scale(${0.4 + 0.8 * (1 - flash)})`,
        }}
      />
      {/* 통합 카드 */}
      <div style={{position: 'absolute', left: 960 - 330, top: cardTop - 20, width: 660, opacity: fuse, transform: `scale(${0.92 + 0.08 * fuse})`}}>
        <div
          style={{
            position: 'relative',
            overflow: 'hidden',
            background: '#16213B',
            border: `2px solid ${C.accent}`,
            borderRadius: 20,
            padding: '24px 30px 26px 40px',
            boxShadow: `0 24px 60px rgba(0,0,0,0.55), 0 0 ${40 * flash + 18}px rgba(251,191,36,0.45)`,
          }}
        >
          <div style={{position: 'absolute', left: 0, top: 0, bottom: 0, width: 10, background: C.accent}} />
          <div style={{display: 'flex', alignItems: 'center', gap: 14}}>
            <Pill color={C.accent} size={24} solid>
              업무 1건
            </Pill>
            <span style={{fontSize: 24, color: C.sub, fontWeight: 700}}>출처: 쪽지 + 배부</span>
          </div>
          <div style={{fontSize: 40, fontWeight: 800, marginTop: 14, letterSpacing: -0.5}}>내부통제 자가진단 제출</div>
          <div style={{display: 'flex', gap: 14, marginTop: 18, transform: `translateY(${(1 - badge) * 16}px)`, opacity: badge}}>
            <Pill color={C.messenger} size={22}>
              쪽지 · 이영희 (가상)
            </Pill>
            <Pill color={C.wm} size={22}>
              배부 · 감사실-456
            </Pill>
          </div>
        </div>
      </div>
      <div style={{position: 'absolute', left: 0, right: 0, top: 170, textAlign: 'center', opacity: badge}}>
        <span style={{fontSize: 44, fontWeight: 800, color: C.accent}}>업무 하나 · 출처 2개</span>
      </div>
      <Note />
    </BG>
  );
};

// ───────────────────────── 08 반복 업무: 2D 월간 캘린더에 매주 금요일 카드가 차례로 뜸
const DAYS = ['일', '월', '화', '수', '목', '금', '토'];
const GRID = {x: 300, y: 250, cw: 190, ch: 112};
// 2026년 11월: 1일이 일요일 (앱 캡처와 같은 달)
const cellDate = (r: number, c: number) => {
  const n = r * 7 + c + 1;
  return n <= 30 ? {d: n, out: false} : {d: n - 30, out: true};
};
const RECUR = [
  {r: 1, c: 5, label: '↻ 주간업무실적', tag: '', base: true},
  {r: 2, c: 5, label: '↻ 주간업무실적', tag: '+1주', base: false},
  {r: 3, c: 5, label: '↻ 주간업무실적', tag: '+2주', base: false},
  {r: 4, c: 5, label: '↻ 주간업무실적', tag: '+3주', base: false},
];
const OTHER: [number, number, string][] = [
  [0, 3, C.urgent], [0, 5, C.portal], [1, 1, C.messenger], [1, 3, C.portal], [2, 1, C.messenger], [0, 1, C.manual],
];

const Recur: React.FC<{dur: number}> = ({dur}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const grid = spring({frame, fps, config: {damping: 200}});
  const colHl = ramp(frame, 22, 34);
  const step = Math.max(8, Math.min(14, (dur - 50) / 5));
  const title = ramp(frame, 26, 40);
  return (
    <BG>
      <div style={{position: 'absolute', left: 0, right: 0, top: 104, textAlign: 'center', opacity: title, transform: `translateY(${(1 - title) * 10}px)`}}>
        <span style={{fontSize: 40, fontWeight: 800, color: C.routine}}>↻ 매주 반복 → 앞으로의 날짜 미리보기</span>
      </div>
      <div style={{position: 'absolute', left: GRID.x, top: GRID.y - 64, opacity: grid, fontSize: 32, fontWeight: 800}}>2026년 11월</div>
      <div style={{position: 'absolute', left: 0, top: 0, opacity: grid, transform: `translateY(${(1 - grid) * 20}px)`}}>
        {/* 금요일 열 강조 */}
        <div
          style={{
            position: 'absolute',
            left: GRID.x + 5 * GRID.cw - 3,
            top: GRID.y - 3,
            width: GRID.cw + 6,
            height: 44 + 5 * GRID.ch + 6,
            borderRadius: 10,
            background: `rgba(251,140,0,${0.1 * colHl})`,
            border: `2px solid rgba(251,140,0,${0.6 * colHl})`,
          }}
        />
        {DAYS.map((d, c) => (
          <div
            key={d}
            style={{
              position: 'absolute',
              left: GRID.x + c * GRID.cw,
              top: GRID.y,
              width: GRID.cw,
              height: 44,
              textAlign: 'center',
              lineHeight: '44px',
              fontSize: 22,
              fontWeight: 700,
              color: c === 0 ? '#F87171' : c === 6 ? '#60A5FA' : C.sub,
            }}
          >
            {d}
          </div>
        ))}
        {Array.from({length: 35}, (_, n) => {
          const r = Math.floor(n / 7);
          const c = n % 7;
          const {d, out} = cellDate(r, c);
          const today = !out && d === 4;
          return (
            <div
              key={n}
              style={{
                position: 'absolute',
                left: GRID.x + c * GRID.cw + 3,
                top: GRID.y + 44 + r * GRID.ch + 3,
                width: GRID.cw - 6,
                height: GRID.ch - 6,
                boxSizing: 'border-box',
                borderRadius: 8,
                background: out ? '#0D1528' : '#111C33',
                border: `1px solid ${today ? '#60A5FA' : '#1F2B45'}`,
                padding: '6px 10px',
                fontSize: 20,
                fontWeight: today ? 800 : 500,
                color: out ? '#475569' : c === 0 ? '#F87171' : c === 6 ? '#60A5FA' : '#CBD5E1',
              }}
            >
              {d}
              {today ? <span style={{fontSize: 16, marginLeft: 6, color: '#60A5FA'}}>오늘</span> : null}
            </div>
          );
        })}
        {OTHER.map(([r, c, col], i) => (
          <div
            key={i}
            style={{
              position: 'absolute',
              left: GRID.x + c * GRID.cw + 12,
              top: GRID.y + 44 + r * GRID.ch + 40 + (r === 0 && c === 5 ? 0 : 0),
              width: GRID.cw - 30,
              height: 14,
              borderRadius: 4,
              background: col,
              opacity: 0.35,
            }}
          />
        ))}
      </div>
      {RECUR.map((it, i) => {
        const p = spring({frame: frame - (12 + i * step + (i ? 18 : 0)), fps, config: {damping: 11, mass: 0.6}});
        const glow = it.base ? 0 : interpolate(frame, [12 + i * step + 18, 12 + i * step + 30, 12 + i * step + 60], [0, 1, 0.3], CL);
        return (
          <div
            key={i}
            style={{
              position: 'absolute',
              left: GRID.x + it.c * GRID.cw + 9,
              top: GRID.y + 44 + it.r * GRID.ch + 40,
              width: GRID.cw - 18,
              boxSizing: 'border-box',
              opacity: Math.min(1, p * 1.5),
              transform: `scale(${Math.max(0, p)})`,
              transformOrigin: '50% 50%',
              background: it.base ? C.routine : '#3B2508',
              border: `2px ${it.base ? 'solid' : 'dashed'} ${C.routine}`,
              borderRadius: 8,
              padding: '4px 8px',
              fontSize: 17,
              fontWeight: 800,
              color: it.base ? '#1C1004' : '#FDBA74',
              whiteSpace: 'nowrap',
              boxShadow: `0 0 ${26 * glow}px ${C.routine}`,
            }}
          >
            {it.label}
            {it.tag ? (
              <span style={{position: 'absolute', right: -12, top: -16, fontSize: 16, background: C.routine, color: '#1C1004', borderRadius: 999, padding: '1px 8px'}}>{it.tag}</span>
            ) : null}
          </div>
        );
      })}
      <Note />
    </BG>
  );
};

export const Motion: React.FC<{id: string; dur: number; shot: Shot}> = ({id, dur, shot}) => {
  // 이 장면 안에서 i번째 문장이 시작하는 프레임 (자막·음성과 맞추기)
  const at = (i: number) => (shot.lines[i] ? Math.round((shot.lines[i].start - shot.start) * 30) : dur);
  if (id === 'desk') return <Desk dur={dur} />;
  if (id === 'flow') return <Flow at={at} />;
  if (id === 'sort') return <Sort dur={dur} at={at} />;
  if (id === 'merge') return <Merge dur={dur} at={at} />;
  if (id === 'recur') return <Recur dur={dur} />;
  return null;
};
