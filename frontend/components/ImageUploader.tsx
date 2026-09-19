'use client';

import React, { useState, useEffect } from 'react';
import { Image as ImageIcon, Upload, X } from 'lucide-react';

interface ImageUploaderProps {
  onImageSelected: (file: File | null) => void;
  selectedImage?: File | null;
}

export const ImageUploader: React.FC<ImageUploaderProps> = ({ onImageSelected, selectedImage }) => {
  const [preview, setPreview] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedImage) {
      setPreview(null);
      return;
    }
    const reader = new FileReader();
    reader.onloadend = () => {
      setPreview(reader.result as string);
    };
    reader.readAsDataURL(selectedImage);
  }, [selectedImage]);

  const handleFileChange = (file: File | null) => {
    if (!file) {
      setPreview(null);
      onImageSelected(null);
      return;
    }

    if (!file.type.startsWith('image/')) {
      alert('Please upload an image file (PNG, JPG, WebP)');
      return;
    }

    onImageSelected(file);
  };

  return (
    <div>
      {preview ? (
        <div className="relative inline-block rounded-xl overflow-hidden border border-slate-200 shadow-sm">
          {/* eslint-disable-next-html-next/no-img-element */}
          <img src={preview} alt="Diagram preview" className="w-24 h-24 object-cover" />
          <button
            onClick={() => handleFileChange(null)}
            className="absolute top-1 right-1 p-1 rounded-full bg-slate-900/70 text-white hover:bg-slate-900 transition-colors"
          >
            <X className="w-3 h-3" />
          </button>
        </div>
      ) : (
        <label className="flex items-center gap-2 px-3 py-1.5 rounded-xl glass-input text-xs font-medium text-slate-600 cursor-pointer hover:text-indigo-600 transition-colors">
          <ImageIcon className="w-4 h-4 text-indigo-500" />
          <span>Attach Diagram / Screenshot</span>
          <input
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => handleFileChange(e.target.files?.[0] || null)}
          />
        </label>
      )}
    </div>
  );
};
