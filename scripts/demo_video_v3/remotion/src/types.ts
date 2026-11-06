export type Rect = [number, number, number, number];

export type View =
  | {k: 'card'; id: string}
  | {k: 'motion'; id: string}
  | {k: 'app'; img: string; zoom: Rect | null; hl: Rect[]; tag: string | null; note: string; size: [number, number]}
  | {k: 'mock'; id: string; hl: boolean}
  | {k: 'fit'};

export type Line = {
  scene: number;
  scene_name: string;
  line: number;
  start: number;
  end: number;
  text: string;
  voice: number | null;
  wav?: string;
  view: View;
};

export type Timeline = {
  fps: number;
  duration: number;
  lines: Line[];
  scenes: {scene: number; name: string; start: number; end: number}[];
};

// 같은 화면이 이어지는 문장들을 한 '샷'으로 묶음 (모션·확대가 문장마다 처음부터 다시 시작하지 않게)
export type Shot = {key: string; start: number; end: number; lines: Line[]};

export const viewKey = (v: View): string => {
  if (v.k === 'app') return `app:${v.img}`;
  if (v.k === 'card' || v.k === 'motion' || v.k === 'mock') return `${v.k}:${v.id}`;
  return v.k;
};

export const toShots = (lines: Line[]): Shot[] => {
  const shots: Shot[] = [];
  for (const ln of lines) {
    const key = viewKey(ln.view);
    const last = shots[shots.length - 1];
    if (last && last.key === key && last.lines[0].scene === ln.scene) {
      last.lines.push(ln);
      last.end = ln.end;
    } else {
      shots.push({key, start: ln.start, end: ln.end, lines: [ln]});
    }
  }
  return shots;
};
