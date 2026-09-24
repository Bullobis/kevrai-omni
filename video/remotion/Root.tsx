import React from "react";
import {Composition} from "remotion";
import {KevraiDemo, type DemoProps} from "./KevraiDemo";

export const RemotionRoot: React.FC = () => {
  const defaultProps: DemoProps = {
    generatedAt: "Local build",
    modelCount: 0,
    engineCount: 0,
  };

  return (
    <Composition
      id="KevraiDemo"
      component={KevraiDemo}
      durationInFrames={210}
      fps={30}
      width={1280}
      height={720}
      defaultProps={defaultProps}
    />
  );
};
