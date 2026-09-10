import { useEffect, useMemo, useRef, type ReactNode } from "react";
import {
  Animated,
  PanResponder,
  StyleSheet,
  View,
  type GestureResponderEvent
} from "react-native";

import { pinchDistance, scaleFromPinch, shouldSwipePreview } from "@/lib/previewZoom";

function eventPinchDist(e: GestureResponderEvent): number {
  const touches = e.nativeEvent.touches;
  if (!touches || touches.length < 2) return 0;
  return pinchDistance(touches[0]!, touches[1]!);
}

export function ZoomablePreview({
  resetKey,
  onSwipe,
  children
}: {
  resetKey: string;
  onSwipe?: (dir: -1 | 1) => void;
  children: ReactNode;
}) {
  const scale = useRef(new Animated.Value(1)).current;
  const tx = useRef(new Animated.Value(0)).current;
  const ty = useRef(new Animated.Value(0)).current;
  const scaleN = useRef(1);
  const txN = useRef(0);
  const tyN = useRef(0);
  const pinchStart = useRef<{ dist: number; scale: number } | null>(null);
  const panStart = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);
  const swipeX = useRef<number | null>(null);

  useEffect(() => {
    scaleN.current = 1;
    txN.current = 0;
    tyN.current = 0;
    scale.setValue(1);
    tx.setValue(0);
    ty.setValue(0);
    pinchStart.current = null;
    panStart.current = null;
    swipeX.current = null;
  }, [resetKey, scale, tx, ty]);

  const responder = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: () => true,
        onMoveShouldSetPanResponder: (_e, g) =>
          g.numberActiveTouches >= 2 || scaleN.current > 1.05 || Math.abs(g.dx) > 10,
        onPanResponderTerminationRequest: () => false,
        onPanResponderGrant: (e) => {
          const n = e.nativeEvent.touches?.length ?? 1;
          if (n >= 2) {
            const dist = eventPinchDist(e);
            pinchStart.current = { dist: dist || 1, scale: scaleN.current };
            swipeX.current = null;
            panStart.current = null;
            return;
          }
          if (scaleN.current > 1.05) {
            panStart.current = {
              x: e.nativeEvent.pageX,
              y: e.nativeEvent.pageY,
              tx: txN.current,
              ty: tyN.current
            };
            swipeX.current = null;
            return;
          }
          swipeX.current = e.nativeEvent.pageX;
        },
        onPanResponderMove: (e, g) => {
          if (g.numberActiveTouches >= 2) {
            const dist = eventPinchDist(e);
            if (!pinchStart.current) {
              pinchStart.current = { dist: dist || 1, scale: scaleN.current };
            }
            const next = scaleFromPinch(pinchStart.current.scale, pinchStart.current.dist, dist);
            scaleN.current = next;
            scale.setValue(next);
            return;
          }
          if (scaleN.current > 1.05 && panStart.current) {
            txN.current = panStart.current.tx + (e.nativeEvent.pageX - panStart.current.x);
            tyN.current = panStart.current.ty + (e.nativeEvent.pageY - panStart.current.y);
            tx.setValue(txN.current);
            ty.setValue(tyN.current);
          }
        },
        onPanResponderRelease: (e) => {
          const wasPinch = Boolean(pinchStart.current);
          pinchStart.current = null;
          panStart.current = null;
          if (wasPinch) {
            if (scaleN.current < 1.05) {
              scaleN.current = 1;
              txN.current = 0;
              tyN.current = 0;
              Animated.parallel([
                Animated.spring(scale, { toValue: 1, useNativeDriver: true, bounciness: 0 }),
                Animated.spring(tx, { toValue: 0, useNativeDriver: true, bounciness: 0 }),
                Animated.spring(ty, { toValue: 0, useNativeDriver: true, bounciness: 0 })
              ]).start();
            }
            swipeX.current = null;
            return;
          }
          if (swipeX.current != null && onSwipe) {
            const dir = shouldSwipePreview(scaleN.current, e.nativeEvent.pageX - swipeX.current);
            if (dir) onSwipe(dir);
          }
          swipeX.current = null;
        },
        onPanResponderTerminate: () => {
          pinchStart.current = null;
          panStart.current = null;
          swipeX.current = null;
        }
      }),
    [onSwipe, scale, tx, ty]
  );

  return (
    <View style={styles.fill} {...responder.panHandlers}>
      <Animated.View
        style={[
          styles.fill,
          { transform: [{ translateX: tx }, { translateY: ty }, { scale }] }
        ]}
        pointerEvents="none"
      >
        {children}
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { width: "100%", height: "100%" }
});
