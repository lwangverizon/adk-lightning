import 'package:flutter/material.dart';

import 'a2ui_examples.dart';
import 'a2ui_view.dart';

/// A reproducible review surface independent of agents and model credentials.
class A2uiGallery extends StatefulWidget {
  const A2uiGallery({super.key});
  @override
  State<A2uiGallery> createState() => _A2uiGalleryState();
}

class _A2uiGalleryState extends State<A2uiGallery> {
  String version = 'v0.9';
  String kind = 'Text';
  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('A2UI primitive gallery')),
    body: Center(
      child: SizedBox(
        width: 680,
        child: ListView(
          padding: const EdgeInsets.all(24),
          children: [
            const Text(
              '18 basic-catalog components. Edit controls locally and inspect button events. '
              'Agent actions are not sent. Media examples load from public sample URLs.',
            ),
            const SizedBox(height: 16),
            Wrap(
              spacing: 20,
              children: [
                DropdownButton<String>(
                  value: version,
                  items: const [
                    DropdownMenuItem(value: 'v0.9', child: Text('v0.9 stable')),
                    DropdownMenuItem(
                      value: 'v1.0',
                      child: Text('v1.0 candidate'),
                    ),
                  ],
                  onChanged: (v) => setState(() => version = v!),
                ),
                DropdownButton<String>(
                  value: kind,
                  items: [
                    for (final name in a2uiExamples.keys)
                      DropdownMenuItem(value: name, child: Text(name)),
                  ],
                  onChanged: (v) => setState(() => kind = v!),
                ),
              ],
            ),
            const SizedBox(height: 20),
            A2uiView(
              key: ValueKey('$version-$kind'),
              a2ui: a2uiExample(kind, version),
            ),
          ],
        ),
      ),
    ),
  );
}
