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
      defaultProps={{
        // variant 改变背景粒子 / 波形的随机种子（定时重渲染时画面动态略有差异）
        variant: 0,
        // buildDate 显示在结尾，体现每次构建的真实日期
        buildDate: "",
        version: "2.9.0",
      }}
    />
  );
};
