"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

import { clampPreviewZoom, pinchDistance, scaleFromPinch, scaleFromWheel } from "../lib/previewZoom";

export function ZoomablePreview({
  resetKey,
  disabled = false,
  children
}: {
  resetKey: string;
  disabled?: boolean;
  children: ReactNode;
}) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const scaleRef = useRef(1);
  const xRef = useRef(0);
  const yRef = useRef(0);
  const pinchRef = useRef<{ dist: number; scale: number } | null>(null);
  const panRef = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);
  const [zoom, setZoom] = useState({ scale: 1, x: 0, y: 0 });

  function apply(scale: number, x: number, y: number) {
    const nextScale = clampPreviewZoom(scale);
    const nextX = nextScale <= 1.02 ? 0 : x;
    const nextY = nextScale <= 1.02 ? 0 : y;
    const next = { scale: nextScale <= 1.02 ? 1 : nextScale, x: nextX, y: nextY };
    scaleRef.current = next.scale;
    xRef.current = next.x;
    yRef.current = next.y;
    setZoom(next);
  }

  useEffect(() => {
    apply(1, 0, 0);
    pinchRef.current = null;
    panRef.current = null;
  }, [resetKey, disabled]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el || disabled) return;

    const onTouchStart = (e: TouchEvent) => {
      if (e.touches.length >= 2) {
        const dist = pinchDistance(e.touches[0]!, e.touches[1]!);
        pinchRef.current = { dist: dist || 1, scale: scaleRef.current };
        panRef.current = null;
        return;
      }
      if (scaleRef.current > 1.05 && e.touches.length === 1) {
        panRef.current = {
          x: e.touches[0]!.clientX,
          y: e.touches[0]!.clientY,
          tx: xRef.current,
          ty: yRef.current
        };
      }
    };

    const onTouchMove = (e: TouchEvent) => {
      if (e.touches.length >= 2) {
        e.preventDefault();
        const dist = pinchDistance(e.touches[0]!, e.touches[1]!);
        if (!pinchRef.current) {
          pinchRef.current = { dist: dist || 1, scale: scaleRef.current };
        }
        apply(scaleFromPinch(pinchRef.current.scale, pinchRef.current.dist, dist), xRef.current, yRef.current);
        return;
      }
      if (panRef.current && e.touches.length === 1) {
        e.preventDefault();
        apply(
          scaleRef.current,
          panRef.current.tx + (e.touches[0]!.clientX - panRef.current.x),
          panRef.current.ty + (e.touches[0]!.clientY - panRef.current.y)
        );
      }
    };

    const onTouchEnd = () => {
      pinchRef.current = null;
      panRef.current = null;
    };

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      apply(scaleFromWheel(scaleRef.current, e.deltaY), xRef.current, yRef.current);
    };

    const onPointerDown = (e: PointerEvent) => {
      if (e.pointerType === "touch" || scaleRef.current <= 1.05) return;
      panRef.current = { x: e.clientX, y: e.clientY, tx: xRef.current, ty: yRef.current };
      el.setPointerCapture(e.pointerId);
    };

    const onPointerMove = (e: PointerEvent) => {
      if (!panRef.current || e.pointerType === "touch") return;
      apply(scaleRef.current, panRef.current.tx + (e.clientX - panRef.current.x), panRef.current.ty + (e.clientY - panRef.current.y));
    };

    const onPointerUp = () => {
      if (pinchRef.current) return;
      panRef.current = null;
    };

    el.addEventListener("touchstart", onTouchStart, { passive: true });
    el.addEventListener("touchmove", onTouchMove, { passive: false });
    el.addEventListener("touchend", onTouchEnd);
    el.addEventListener("touchcancel", onTouchEnd);
    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("pointerdown", onPointerDown);
    el.addEventListener("pointermove", onPointerMove);
    el.addEventListener("pointerup", onPointerUp);
    el.addEventListener("pointercancel", onPointerUp);
    return () => {
      el.removeEventListener("touchstart", onTouchStart);
      el.removeEventListener("touchmove", onTouchMove);
      el.removeEventListener("touchend", onTouchEnd);
      el.removeEventListener("touchcancel", onTouchEnd);
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("pointerdown", onPointerDown);
      el.removeEventListener("pointermove", onPointerMove);
      el.removeEventListener("pointerup", onPointerUp);
      el.removeEventListener("pointercancel", onPointerUp);
    };
  }, [disabled]);

  return (
    <div
      ref={wrapRef}
      data-zoomable-preview="1"
      data-zoom-scale={zoom.scale.toFixed(2)}
      onWheel={(e) => {
        if (disabled) return;
        apply(scaleFromWheel(scaleRef.current, e.deltaY), xRef.current, yRef.current);
      }}
      style={{
        width: "100%",
        height: "100%",
        overflow: "hidden",
        touchAction: disabled ? "auto" : "none",
        cursor: disabled ? undefined : zoom.scale > 1.05 ? "grab" : "zoom-in"
      }}
    >
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          transform: `translate(${zoom.x}px, ${zoom.y}px) scale(${zoom.scale})`,
          transformOrigin: "center center"
        }}
      >
        {children}
      </div>
    </div>
  );
}
