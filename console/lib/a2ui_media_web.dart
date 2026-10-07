import 'package:flutter/material.dart';
import 'package:web/web.dart' as web;

/// Native browser controls: no autoplay, scripts, embeds or HTML from the model.
Widget a2uiMedia(
  String kind,
  String source, {
  String? poster,
  String? description,
}) {
  return SizedBox(
    height: kind == 'Video' ? 220 : 64,
    width: double.infinity,
    child: HtmlElementView.fromTagName(
      key: ValueKey('$kind:$source:$poster'),
      tagName: kind == 'Video' ? 'video' : 'audio',
      onElementCreated: (element) => configureA2uiMedia(
        element as web.HTMLMediaElement,
        kind,
        source,
        poster: poster,
        description: description,
      ),
    ),
  );
}

/// Shared native-element configuration, exercised with the browser's decoder.
void configureA2uiMedia(
  web.HTMLMediaElement media,
  String kind,
  String source, {
  String? poster,
  String? description,
}) {
  media.src = source;
  media.controls = true;
  media.preload = 'none';
  media.style.width = '100%';
  media.style.height = '100%';
  media.setAttribute('aria-label', description ?? kind);
  if (kind == 'Video' && poster != null) {
    (media as web.HTMLVideoElement).poster = poster;
  }
}

Widget a2uiSvg(String path) => SizedBox(
  width: 24,
  height: 24,
  child: HtmlElementView.fromTagName(
    key: ValueKey(path),
    tagName: 'span',
    onElementCreated: (element) =>
        configureA2uiSvg(element as web.HTMLElement, path),
  ),
);

void configureA2uiSvg(web.HTMLElement element, String path) {
  final svg = web.document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', '24');
  svg.setAttribute('height', '24');
  final shape = web.document.createElementNS(
    'http://www.w3.org/2000/svg',
    'path',
  );
  shape.setAttribute('d', path);
  shape.setAttribute('fill', 'currentColor');
  svg.appendChild(shape);
  element.appendChild(svg);
}
