import 'package:flutter/material.dart';

Widget a2uiMedia(
  String kind,
  String source, {
  String? poster,
  String? description,
}) => Text('$kind: browser playback required ($source)');

Widget a2uiSvg(String path) =>
    const Text('SVG icon: browser rendering required');
