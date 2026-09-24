import React from "react";
import { Composition } from "remotion";
import { KevraiPromo } from "./compositions/KevraiPromo";

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="KevraiPromo"
      component={KevraiPromo}
      durationInFrames={900}
      fps={30}
      width={1920}
      height={1080}
    />
  );
};
