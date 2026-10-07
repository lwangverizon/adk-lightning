import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:adkl_console/widget_card.dart';

void main() {
  testWidgets('shared quiz converter renders in the chat widget card', (t) async {
    // Captured from adkl.a2ui.convert_all with adkl/tutoring@1, not a
    // project converter. Python regression tests also pin this fixture.
    final widget = jsonDecode(File('test/fixtures/shared_quiz.json').readAsStringSync())
        as Map<String, dynamic>;
    await t.pumpWidget(MaterialApp(home: Scaffold(body: SingleChildScrollView(
      child: WidgetCard(widget_: widget),
    ))));
    expect(find.text('What is 2 + 3?'), findsOneWidget);
    expect(find.text('A. 4'), findsOneWidget);
    expect(find.text('B. 5'), findsOneWidget);
    expect(find.textContaining('answer submission is unavailable'), findsOneWidget);
    expect(t.takeException(), isNull);
  });
}
