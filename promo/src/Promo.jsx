import React from "react";
import { AbsoluteFill, Sequence } from "remotion";
import { Background } from "./components/Background.jsx";
import { SceneIntro } from "./scenes/SceneIntro.jsx";
import { SceneStats } from "./scenes/SceneStats.jsx";
import { SceneMarket } from "./scenes/SceneMarket.jsx";
import { SceneHardware } from "./scenes/SceneHardware.jsx";
import { SceneAgent } from "./scenes/SceneAgent.jsx";
import { SceneVideo } from "./scenes/SceneVideo.jsx";
import { SceneEngines } from "./scenes/SceneEngines.jsx";
import { SceneOutro } from "./scenes/SceneOutro.jsx";
import data from "./data.json";

export const FPS = 30;
const OVERLAP = 12;

const SCENES = [
  { dur: 110, Comp: SceneIntro },
  { dur: 90, Comp: SceneStats },
  { dur: 160, Comp: SceneMarket },
  { dur: 100, Comp: SceneHardware },
  { dur: 130, Comp: SceneAgent },
  { dur: 130, Comp: SceneVideo },
  { dur: 122, Comp: SceneEngines },
  { dur: 125, Comp: SceneOutro },
];

export const TOTAL_FRAMES = SCENES.reduce((acc, s) => acc + s.dur, 0);

export const Promo = () => {
  let cursor = 0;
  return (
    <AbsoluteFill style={{ backgroundColor: "#0d0e11" }}>
      <Background />
      {SCENES.map((s, i) => {
        const from = i === 0 ? 0 : cursor - OVERLAP;
        const seqDur = s.dur + OVERLAP;
        const node = (
          <Sequence key={i} from={from} durationInFrames={seqDur}>
            <s.Comp durationInFrames={s.dur} data={data} />
          </Sequence>
        );
        cursor += s.dur;
        return node;
      })}
    </AbsoluteFill>
  );
};
