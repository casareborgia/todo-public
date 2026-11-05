import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig, Easing} from 'remotion';
import {C, FONT} from '../theme';

const Rise: React.FC<{delay?: number; children: React.ReactNode; style?: React.CSSProperties}> = ({delay = 0, children, style}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const p = spring({frame: frame - delay, fps, config: {damping: 200}});
  return <div style={{opacity: p, transform: `translateY(${(1 - p) * 30}px)`, ...style}}>{children}</div>;
};

const Glow: React.FC = () => {
  const frame = useCurrentFrame();
  const a = (frame / 30) * 0.15;
  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(1200px 700px at ${50 + 10 * Math.sin(a)}% ${40 + 6 * Math.cos(a)}%, rgba(30,136,229,0.22), transparent 60%),
                     radial-gradient(900px 600px at 80% 85%, rgba(251,140,0,0.14), transparent 60%)`,
      }}
    />
  );
};

const center: React.CSSProperties = {justifyContent: 'center', alignItems: 'center', fontFamily: FONT, color: C.ink, textAlign: 'center', paddingBottom: 120};

const Dots: React.FC = () => (
  <div style={{display: 'flex', gap: 18, justifyContent: 'center', marginTop: 34}}>
    {[
      ['공문', C.portal],
      ['쪽지', C.messenger],
      ['배부', C.wm],
      ['반복', C.routine],
      ['긴급', C.urgent],
    ].map(([t, c]) => (
      <div key={t} style={{fontSize: 28, padding: '8px 22px', borderRadius: 999, border: `2px solid ${c}`, color: c as string}}>
        {t}
      </div>
    ))}
  </div>
);

// 문서·쪽지가 쌓이는 그림 (문제 장면)
const Pile: React.FC = () => {
  const frame = useCurrentFrame();
  const items = Array.from({length: 36}, (_, i) => i);
  return (
    <div style={{position: 'relative', width: 1100, height: 330, margin: '40px auto 0'}}>
      {items.map((i) => {
        const appear = i * 2.2;
        const o = interpolate(frame, [appear, appear + 6], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
        const col = i % 3 === 0 ? C.portal : i % 3 === 1 ? C.messenger : C.wm;
        const x = ((i * 137) % 1000) + 20;
        const y = ((i * 71) % 260) + 10;
        const lost = i === 23;
        return (
          <div
            key={i}
            style={{
              position: 'absolute',
              left: x,
              top: y,
              width: 120,
              height: 64,
              borderRadius: 8,
              background: '#E2E8F0',
              borderLeft: `8px solid ${col}`,
              opacity: lost ? o * 0.35 : o,
              transform: `rotate(${((i * 37) % 21) - 10}deg)`,
              boxShadow: '0 8px 18px rgba(0,0,0,0.4)',
            }}
          />
        );
      })}
    </div>
  );
};

export const Cards: React.FC<{id: string; dur: number}> = ({id}) => {
  const frame = useCurrentFrame();
  if (id === 'title') {
    return (
      <AbsoluteFill style={{background: C.bg}}>
        <Glow />
        <AbsoluteFill style={center}>
          <Rise>
            <div style={{fontSize: 30, color: C.accent, letterSpacing: 2, marginBottom: 26}}>사내 업무 캘린더 &amp; 투두 매니저</div>
          </Rise>
          <Rise delay={6}>
            <div style={{fontSize: 104, fontWeight: 800, lineHeight: 1.18, letterSpacing: -3}}>
              들어오는 모든 업무,
              <br />
              <span style={{color: C.accent}}>캘린더 하나로</span>
            </div>
          </Rise>
          <Rise delay={14}>
            <Dots />
          </Rise>
        </AbsoluteFill>
      </AbsoluteFill>
    );
  }
  if (id === 'problem' || id === 'problem2' || id === 'problem3') {
    const big =
      id === 'problem' ? '하루에도 수십 건' : id === 'problem2' ? '한 달 반 뒤에야 발견한 업무' : '업무 누락 방지 도구';
    const small =
      id === 'problem' ? '공문 · 메신저 쪽지 · 배부 문서' : id === 'problem2' ? '묻힌 업무 하나가 남긴 것' : '내부망 PC에서, 혼자 쓰는 업무 캘린더';
    return (
      <AbsoluteFill style={{background: C.bg}}>
        <Glow />
        <AbsoluteFill style={center}>
          <Rise>
            <div style={{fontSize: 34, color: C.sub, marginBottom: 18}}>{small}</div>
          </Rise>
          <Rise delay={5}>
            <div style={{fontSize: 92, fontWeight: 800, letterSpacing: -2, color: id === 'problem2' ? C.urgent : C.ink}}>{big}</div>
          </Rise>
          {id === 'problem' ? <Pile /> : null}
          {id === 'problem3' ? (
            <Rise delay={12}>
              <Dots />
            </Rise>
          ) : null}
        </AbsoluteFill>
      </AbsoluteFill>
    );
  }
  if (id === 'security') {
    const items = ['외부 통신 없음', 'Python 표준 라이브러리만', '설치 없이 실행 · 관리자 권한 불필요', '메신저·업무관리시스템은 화면 글자만 읽기'];
    return (
      <AbsoluteFill style={{background: C.bg}}>
        <Glow />
        <AbsoluteFill style={center}>
          <Rise>
            <div style={{fontSize: 60, fontWeight: 800, marginBottom: 40}}>내부망 보안 환경에 맞게</div>
          </Rise>
          <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 22, width: 1600}}>
            {items.map((t, i) => (
              <Rise key={t} delay={6 + i * 5}>
                <div style={{fontSize: 31, padding: '26px 30px', borderRadius: 18, whiteSpace: 'nowrap', background: '#111C33', border: `1px solid ${C.line}`, textAlign: 'left'}}>
                  <span style={{color: C.messenger, marginRight: 16}}>✔</span>
                  {t}
                </div>
              </Rise>
            ))}
          </div>
        </AbsoluteFill>
      </AbsoluteFill>
    );
  }
  if (id === 'end' || id === 'endcard') {
    const o = id === 'endcard' ? interpolate(frame, [0, 10, 70, 90], [1, 1, 1, 0], {extrapolateRight: 'clamp'}) : 1;
    return (
      <AbsoluteFill style={{background: C.bg, opacity: o}}>
        <Glow />
        <AbsoluteFill style={center}>
          <Rise>
            <div style={{fontSize: 96, fontWeight: 800, letterSpacing: -2}}>
              받은 업무, <span style={{color: C.accent}}>이제 놓치지 않습니다</span>
            </div>
          </Rise>
          <Rise delay={8}>
            <div style={{fontSize: 32, color: C.sub, marginTop: 30}}>사내 업무 캘린더 &amp; 투두 매니저</div>
          </Rise>
        </AbsoluteFill>
      </AbsoluteFill>
    );
  }
  return null;
};

export {Rise};
// Easing을 다른 장면에서 쓰도록 다시 내보냄
export {Easing};
