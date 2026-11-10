/// Main application entrypoint for Hemory Flutter client.
/// Sets up Riverpod ProviderScope and award-tier Obsidian Dark theme.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'ui/theme/hemory_theme.dart';
import 'ui/timeline/memory_timeline_scene.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(
    const ProviderScope(
      child: HemoryApp(),
    ),
  );
}

class HemoryApp extends StatelessWidget {
  const HemoryApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Hemory',
      debugShowCheckedModeBanner: false,
      theme: HemoryTheme.darkTheme,
      home: const MemoryTimelineScene(),
    );
  }
}
