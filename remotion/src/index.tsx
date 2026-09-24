import React from "react";
import { Composition, registerRoot } from "remotion";
import { LogoIntro, type LogoIntroProps } from "./compositions/LogoIntro";
import {
  VersionPoster,
  type VersionPosterProps,
} from "./compositions/VersionPoster";

// 默认 props（渲染时可通过 --props 覆盖）
const today = new Date().toISOString().slice(0, 10);

const defaultLogoIntroProps: LogoIntroProps = {
  version: "v3.0.0",
  date: today,
};

const defaultVersionPosterProps: VersionPosterProps = {
  version: "v3.0.0",
  date: today,
  highlights: [
    "New LLM engine with 2x faster inference",
    "Redesigned local-first UI",
    "3D preview & export improvements",
  ],
};

const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="LogoIntro"
        component={LogoIntro}
        durationInFrames={300} // 10s @ 30fps
        fps={30}
        width={1920}
        height={1080}
        defaultProps={defaultLogoIntroProps}
      />
      <Composition
        id="VersionPoster"
        component={VersionPoster}
        durationInFrames={450} // 15s @ 30fps
        fps={30}
        width={1080}
        height={1080}
        defaultProps={defaultVersionPosterProps}
      />
    </>
  );
};

registerRoot(RemotionRoot);
