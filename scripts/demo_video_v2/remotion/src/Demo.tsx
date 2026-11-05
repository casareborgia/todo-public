import React from 'react';
import {AbsoluteFill, Audio, Sequence, interpolate, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import tl from '../public/timeline.json';
import {C, FONT} from './theme';
import {Timeline, toShots, Shot} from './types';
import {AppShot} from './scenes/AppShot';
import {Cards} from './scenes/Cards';
import {MockList} from './scenes/MockList';
import {FitMontage} from './scenes/FitMontage';
import {Motion} from './scenes/Motion';

const T = tl as unknown as Timeline;
const FPS = 30;
const f = (s: number) => Math.round(s * FPS);

const ShotView: React.FC<{shot: Shot}> = ({shot}) => {
  const v = shot.lines[0].view;
  const frame = useCurrentFrame();
  const dur = f(shot.end - shot.start);
  const opacity = interpolate(frame, [0, 8, dur - 6, dur], [0, 1, 1, 0.0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  let body: React.ReactNode = null;
  if (v.k === 'app') body = <AppShot shot={shot} />;
  else if (v.k === 'card') body = <Cards id={v.id} dur={dur} />;
  else if (v.k === 'motion') body = <Motion id={v.id} dur={dur} shot={shot} />;
  else if (v.k === 'mock') body = <MockList shot={shot} />;
  else if (v.k === 'fit') body = <FitMontage dur={dur} />;
  return <AbsoluteFill style={{opacity}}>{body}</AbsoluteFill>;
};

const Subtitle: React.FC<{text: string; dur: number}> = ({text, dur}) => {
  const frame = useCurrentFrame();
  const o = interpolate(frame, [0, 5, dur - 4, dur], [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', paddingBottom: 46}}>
      <div
        style={{
          opacity: o,
          fontFamily: FONT,
          fontSize: 40,
          fontWeight: 700,
          color: C.ink,
          background: 'rgba(2,6,23,0.78)',
          padding: '14px 34px',
          borderRadius: 14,
          maxWidth: 1600,
          textAlign: 'center',
          lineHeight: 1.4,
          letterSpacing: -0.5,
        }}
      >
        {text}
      </div>
    </AbsoluteFill>
  );
};

const SceneChip: React.FC<{n: number; name: string}> = ({n, name}) => (
  <div
    style={{
      position: 'absolute',
      top: 26,
      right: 34,
      fontFamily: FONT,
      fontSize: 22,
      color: C.sub,
      letterSpacing: 0.5,
    }}
  >
    {String(n).padStart(2, '0')} · {name}
  </div>
);

export const Demo: React.FC = () => {
  const {durationInFrames} = useVideoConfig();
  const shots = toShots(T.lines);
  return (
    <AbsoluteFill style={{backgroundColor: C.bg, fontFamily: FONT}}>
      {shots.map((s, i) => (
        <Sequence key={i} from={f(s.start)} durationInFrames={f(s.end - s.start)} name={s.key}>
          <ShotView shot={s} />
        </Sequence>
      ))}
      {T.scenes.slice(1).map((s) => (
        <Sequence key={`c${s.scene}`} from={f(s.start)} durationInFrames={f(s.end - s.start)}>
          <SceneChip n={s.scene} name={s.name} />
        </Sequence>
      ))}
      {T.lines.map((ln, i) => (
        <Sequence key={`t${i}`} from={f(ln.start)} durationInFrames={f(ln.end - ln.start)}>
          <Subtitle text={ln.text} dur={f(ln.end - ln.start)} />
        </Sequence>
      ))}
      {T.lines.map((ln, i) =>
        ln.wav ? (
          <Sequence key={`a${i}`} from={f(ln.start) + 6} durationInFrames={f(ln.end - ln.start)}>
            <Audio src={staticFile(ln.wav)} volume={1} />
          </Sequence>
        ) : null,
      )}
      <Audio
        src={staticFile('bgm.wav')}
        volume={(fr) => interpolate(fr, [0, 45, durationInFrames - 90, durationInFrames], [0, 0.11, 0.11, 0], {extrapolateRight: 'clamp'})}
      />
      {/* 끝 카드 */}
      <Sequence from={f(T.lines[T.lines.length - 1].end)}>
        <Cards id="endcard" dur={90} />
      </Sequence>
    </AbsoluteFill>
  );
};
