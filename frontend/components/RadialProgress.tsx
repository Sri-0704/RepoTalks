'use client';

import React from 'react';
import { motion } from 'framer-motion';

interface RadialProgressProps {
  score: number; // 0 to 100
  label: string;
  sublabel?: string;
  size?: number;
  strokeWidth?: number;
  color?: string; // tailwind stroke color or gradient class
}

export const RadialProgress: React.FC<RadialProgressProps> = ({
  score,
  label,
  sublabel,
  size = 120,
  strokeWidth = 10,
  color = 'stroke-indigo-600',
}) => {
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = circumference - (score / 100) * circumference;

  return (
    <div className="flex flex-col items-center justify-center">
      <div className="relative" style={{ width: size, height: size }}>
        <svg className="w-full h-full transform -rotate-90">
          {/* Background circle */}
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            className="stroke-slate-200/80"
            strokeWidth={strokeWidth}
            fill="transparent"
          />
          {/* Progress circle */}
          <motion.circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            className={color}
            strokeWidth={strokeWidth}
            strokeDasharray={circumference}
            initial={{ strokeDashoffset: circumference }}
            animate={{ strokeDashoffset }}
            transition={{ duration: 1.2, ease: 'easeOut' }}
            strokeLinecap="round"
            fill="transparent"
          />
        </svg>

        {/* Center text score */}
        <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
          <span className="text-2xl font-extrabold text-slate-800 tracking-tight">
            {score}
          </span>
          <span className="text-[10px] font-semibold uppercase text-slate-400">/ 100</span>
        </div>
      </div>

      <div className="mt-3 text-center">
        <div className="font-semibold text-xs text-slate-700">{label}</div>
        {sublabel && <div className="text-[11px] text-slate-400 font-normal">{sublabel}</div>}
      </div>
    </div>
  );
};
