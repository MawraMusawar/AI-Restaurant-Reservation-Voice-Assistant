"use client";

import { useEffect, useState } from "react";

const SAMPLE_INTERVAL_MS = 100;
// Speech RMS is usually well below 0.25; scale so normal speech fills the meter.
const RMS_GAIN = 4;

/**
 * Live audio level (0..1) of a MediaStreamTrack, measured with the Web Audio API.
 *
 * Needed for the assistant's audio: with the SmallWebRTC transport, the
 * client's `RemoteAudioLevel` event never fires (it is only computed for
 * WebSocket-style playback), so the remote WebRTC track is measured here.
 * The analyser is not connected to the speakers. Playback is handled
 * separately by <PipecatClientAudio />.
 */
export function useTrackAudioLevel(track: MediaStreamTrack | null | undefined): number {
  const [level, setLevel] = useState(0);

  useEffect(() => {
    if (!track) return;
    const context = new AudioContext();
    const source = context.createMediaStreamSource(new MediaStream([track]));
    const analyser = context.createAnalyser();
    analyser.fftSize = 512;
    source.connect(analyser);
    const samples = new Float32Array(analyser.fftSize);
    void context.resume();

    const id = window.setInterval(() => {
      analyser.getFloatTimeDomainData(samples);
      let sumSquares = 0;
      for (const s of samples) sumSquares += s * s;
      setLevel(Math.min(1, Math.sqrt(sumSquares / samples.length) * RMS_GAIN));
    }, SAMPLE_INTERVAL_MS);

    return () => {
      window.clearInterval(id);
      source.disconnect();
      void context.close();
      setLevel(0);
    };
  }, [track]);

  return track ? level : 0;
}
