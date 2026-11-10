/// Award-tier visual design tokens and theme configuration for Hemory.
/// Conforms to Awwwards / Webby standards: Obsidian dark mode, frosted glass blur,
/// and tabular figures to eliminate layout jitter.
library;

import 'dart:ui';
import 'package:flutter/material.dart';

class HemoryColors {
  // Dark Palette (Deep Obsidian)
  static const Color darkBg = Color(0xFF0D1117);
  static const Color darkSurface1 = Color(0xFF161B22);
  static const Color darkSurface2 = Color(0xFF21262D);
  static const Color darkBorder = Color(0xFF30363D);

  // Light Palette (Pristine Canvas)
  static const Color lightBg = Color(0xFFF8F9FA);
  static const Color lightSurface1 = Color(0xFFFFFFFF);
  static const Color lightSurface2 = Color(0xFFF1F3F5);
  static const Color lightBorder = Color(0xFFE5E7EB);

  // Accents
  static const Color sagePrimary = Color(0xFF61BBA6);
  static const Color sageSecondary = Color(0xFF38D9A9);
  static const Color sageGlow = Color(0x3361BBA6);
  static const Color liveCoral = Color(0xFFFF4D4D);
  static const Color liveCoralGlow = Color(0x66FF4D4D);
  static const Color idleMuted = Color(0x228B949E);

  // Text
  static const Color textLightPrimary = Color(0xFFF0F6FC);
  static const Color textLightSecondary = Color(0xFF8B949E);
  static const Color textLightMuted = Color(0xFF6E7681);
  static const Color textDarkPrimary = Color(0xFF111827);
  static const Color textDarkSecondary = Color(0xFF6B7280);

  // Glass Tint
  static const Color darkGlassTint = Color(0xB8161B22); // rgba(22, 27, 34, 0.72)
  static const Color lightGlassTint = Color(0xB8FFFFFF);
}

class HemoryTheme {
  static const double maxWidth = 680.0; // Responsive desktop/tablet max-width

  static ThemeData get darkTheme {
    return ThemeData(
      brightness: Brightness.dark,
      scaffoldBackgroundColor: HemoryColors.darkBg,
      primaryColor: HemoryColors.sagePrimary,
      colorScheme: const ColorScheme.dark(
        primary: HemoryColors.sagePrimary,
        secondary: HemoryColors.sageSecondary,
        surface: HemoryColors.darkSurface1,
        error: HemoryColors.liveCoral,
        outline: HemoryColors.darkBorder,
        onPrimary: Colors.black,
        onSurface: HemoryColors.textLightPrimary,
      ),
      fontFamily: 'Inter',
      textTheme: const TextTheme(
        displayLarge: TextStyle(
          fontSize: 22.0,
          fontWeight: FontWeight.w600,
          letterSpacing: -0.5,
          color: HemoryColors.textLightPrimary,
          fontFeatures: [FontFeature.tabularFigures()],
        ),
        titleLarge: TextStyle(
          fontSize: 16.0,
          fontWeight: FontWeight.w600,
          letterSpacing: -0.2,
          color: HemoryColors.textLightPrimary,
        ),
        titleMedium: TextStyle(
          fontSize: 14.0,
          fontWeight: FontWeight.w600,
          color: HemoryColors.textLightPrimary,
        ),
        bodyMedium: TextStyle(
          fontSize: 14.0,
          fontWeight: FontWeight.w400,
          height: 1.45,
          color: HemoryColors.textLightSecondary,
        ),
        labelSmall: TextStyle(
          fontSize: 11.0,
          fontWeight: FontWeight.w500,
          color: HemoryColors.textLightMuted,
          fontFeatures: [FontFeature.tabularFigures()],
        ),
      ),
    );
  }

  /// Frosted glass container decoration.
  static BoxDecoration frostedBoxDecoration({
    Color? color,
    Color? borderColor,
    double radius = 16.0,
  }) {
    return BoxDecoration(
      color: color ?? HemoryColors.darkGlassTint,
      borderRadius: BorderRadius.circular(radius),
      border: Border.all(
        color: borderColor ?? HemoryColors.darkBorder,
        width: 0.8,
      ),
      boxShadow: const [
        BoxShadow(
          color: Color(0x1F000000),
          blurRadius: 12.0,
          offset: Offset(0, 4),
        ),
      ],
    );
  }
}
