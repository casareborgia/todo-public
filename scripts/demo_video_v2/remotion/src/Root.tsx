import React from 'react';
import {Composition} from 'remotion';
import {Demo} from './Demo';
import tl from '../public/timeline.json';
import type {Timeline} from './types';

const T = tl as unknown as Timeline;

export const Root: React.FC = () => (
  <>
    <Composition id="DemoV2" component={Demo} durationInFrames={Math.ceil(T.duration * 30)} fps={30} width={1920} height={1080} />
  </>
);
