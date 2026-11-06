import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
import {C, FONT} from '../theme';
import {Shot} from '../types';

// 업무관리시스템 결재시행 목록 "시연용 재현 화면": 실제 화면의 로고·메뉴·색은 따라 그리지 않고 일반 표 모양만
const ROWS = [
  {kind: '수신', role: '업무담당자', title: '2026년 내부통제 자가진단 결과 제출 시행 알림', no: '감사실-456', dept: '가상감사실', at: '09:12'},
  {kind: '수신', role: '업무담당자', title: '2027년 정보보호 교육 계획 제출', no: '정보보호팀-88', dept: '가상정보보호팀', at: '09:30'},
  {kind: '수신', role: '업무관리자', title: '부서별 사무공간 재배치 의견 조회', no: '총무팀-301', dept: '가상총무팀', at: '10:02'},
  {kind: '생산', role: '기안', title: '11월 부서 회의 결과 보고', no: '기획팀-77', dept: '기획팀', at: '08:40'},
];
const COLS = ['문서종류', '처리구분', '제목', '문서번호', '입안부서', '수신일시'];
const W = [130, 150, 640, 190, 190, 150];

export const MockList: React.FC<{shot: Shot}> = ({shot}) => {
  const frame = useCurrentFrame();
  const t = frame / 30;
  const v = shot.lines[0].view as {k: 'mock'; id: string; hl: boolean};
  // 이 샷에서 강조가 켜지는 문장의 시작
  const hlLine = shot.lines.find((ln) => (ln.view as typeof v).hl);
  const hlOn = hlLine ? interpolate(t + shot.start - hlLine.start, [0.4, 1.0], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}) : 0;
  const readonly = v.id === 'readonly';
  const rowIn = (i: number) => interpolate(frame, [8 + i * 6, 18 + i * 6], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const scan = readonly ? (frame % 90) / 90 : -1;
  return (
    <AbsoluteFill style={{background: C.bg, fontFamily: FONT}}>
      <div style={{position: 'absolute', left: 160, top: 110, width: 1600, borderRadius: 14, overflow: 'hidden', background: '#F8FAFC', boxShadow: '0 30px 80px rgba(0,0,0,0.5)'}}>
        <div style={{background: '#E2E8F0', padding: '18px 28px', fontSize: 30, fontWeight: 700, color: '#0F172A', display: 'flex', justifyContent: 'space-between'}}>
          <span>전자문서 &gt; 결재 &gt; 결재시행</span>
          <span style={{fontSize: 22, color: '#64748B', fontWeight: 400}}>시연용 재현 화면</span>
        </div>
        <div style={{display: 'flex', background: '#CBD5E1', fontSize: 24, fontWeight: 700, color: '#1E293B'}}>
          {COLS.map((c, i) => (
            <div key={c} style={{width: W[i], padding: '14px 16px'}}>
              {c}
            </div>
          ))}
        </div>
        {ROWS.map((r, i) => {
          const isTask = r.kind === '수신' && r.role === '업무담당자';
          const isReview = r.kind === '수신' && r.role === '업무관리자';
          const bg = hlOn > 0 && isTask ? `rgba(20,184,166,${0.22 * hlOn})` : hlOn > 0 && isReview ? `rgba(251,191,36,${0.2 * hlOn})` : 'transparent';
          return (
            <div key={i} style={{display: 'flex', fontSize: 25, color: '#0F172A', borderTop: '1px solid #E2E8F0', background: bg, opacity: rowIn(i), position: 'relative'}}>
              {[r.kind, r.role, r.title, r.no, r.dept, `11-04 ${r.at}`].map((c, j) => (
                <div key={j} style={{width: W[j], padding: '18px 16px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', fontWeight: j === 1 && (isTask || isReview) && hlOn > 0 ? 700 : 400}}>
                  {c}
                </div>
              ))}
              {hlOn > 0 && (isTask || isReview) ? (
                <div style={{position: 'absolute', right: -4, top: 12, transform: `translateX(${(1 - hlOn) * 30}px)`, opacity: hlOn, fontSize: 22, fontWeight: 700, color: '#fff', background: isTask ? C.wm : '#D97706', padding: '4px 14px', borderRadius: 999}}>
                  {isTask ? '→ 업무 등록' : '→ 확인 필요'}
                </div>
              ) : null}
            </div>
          );
        })}
        {scan >= 0 ? (
          <div style={{position: 'absolute', left: 0, right: 0, top: 120 + scan * 300, height: 4, background: 'rgba(20,184,166,0.6)', boxShadow: '0 0 24px rgba(20,184,166,0.9)'}} />
        ) : null}
      </div>
      {readonly ? (
        <div style={{position: 'absolute', left: 0, right: 0, top: 620, display: 'flex', justifyContent: 'center', gap: 26, opacity: interpolate(frame, [10, 22], [0, 1], {extrapolateRight: 'clamp'})}}>
          {['🔒 읽기 전용', '문서를 열지 않음', '결재·클릭하지 않음', '서버에 요청하지 않음'].map((x) => (
            <div key={x} style={{fontSize: 32, color: C.ink, background: '#111C33', border: `1px solid ${C.line}`, padding: '16px 28px', borderRadius: 14}}>
              {x}
            </div>
          ))}
        </div>
      ) : null}
      <div style={{position: 'absolute', right: 34, bottom: 14, fontSize: 20, color: C.sub}}>※ 업무관리시스템 화면은 시연용 재현 화면이며, 업무 데이터는 가상입니다</div>
    </AbsoluteFill>
  );
};
