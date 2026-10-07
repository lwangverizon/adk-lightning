@TestOn('browser')
library;

import 'dart:convert';
import 'dart:js_interop';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:web/web.dart' as web;

import 'package:adkl_console/a2ui_media_web.dart';

void main() {
  test(
    'native audio configuration decodes and plays a generated WAV',
    () async {
      final bytes = Uint8List(8044)..fillRange(44, 8044, 128);
      final data = ByteData.sublistView(bytes);
      void ascii(int offset, String text) =>
          bytes.setRange(offset, offset + text.length, text.codeUnits);
      ascii(0, 'RIFF');
      data.setUint32(4, 8036, Endian.little);
      ascii(8, 'WAVEfmt ');
      data.setUint32(16, 16, Endian.little);
      data.setUint16(20, 1, Endian.little);
      data.setUint16(22, 1, Endian.little);
      data.setUint32(24, 8000, Endian.little);
      data.setUint32(28, 8000, Endian.little);
      data.setUint16(32, 1, Endian.little);
      data.setUint16(34, 8, Endian.little);
      ascii(36, 'data');
      data.setUint32(40, 8000, Endian.little);
      final element =
          web.document.createElement('audio') as web.HTMLAudioElement;
      configureA2uiMedia(
        element,
        'AudioPlayer',
        'data:audio/wav;base64,${base64Encode(bytes)}',
      );
      web.document.body!.appendChild(element);
    addTearDown(() => element.remove());
      expect(element.controls, isTrue);
      expect(element.autoplay, isFalse);
      expect(element.preload, 'none');
      element.muted = true;
      await element.play().toDart;
      await Future<void>.delayed(const Duration(milliseconds: 150));
      expect(element.error, isNull);
      expect(element.currentTime, greaterThan(0));
      element.pause();
    },
  );

  test(
    'video and SVG configuration uses native attributes, never HTML parsing',
    () {
      final video = web.document.createElement('video') as web.HTMLVideoElement;
      configureA2uiMedia(video, 'Video', 'https://example.invalid/video.mp4');
      expect(video.controls, isTrue);
      expect(video.autoplay, isFalse);
      expect(video.preload, 'none');
      final svg = web.document.createElement('span') as web.HTMLElement;
      configureA2uiSvg(svg, 'M0 0 L24 0 L12 24 Z');
      expect(
        svg.querySelector('svg path')?.getAttribute('d'),
        'M0 0 L24 0 L12 24 Z',
      );
      expect(svg.querySelector('svg script'), isNull);
    },
  );
}
