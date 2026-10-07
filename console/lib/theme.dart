import 'package:flutter/material.dart';

/// Material 3 console tokens. Neutral surfaces keep source and diagnostics calm;
/// primary actions, failures and attention have distinct semantic roles.
/// Layout adapts to available space in ConsoleShell, independently of branding.
/// Roboto is Flutter's default Material face; no unbundled font is assumed.
class AdklTheme {
  const AdklTheme._();

  static const brandIndigo = Color(0xFF4758A9);
  static const errorCrimson = Color(0xFF8E0009);
  static const attentionAmber = Color(0xFF9A6400);

  /// Positive status accent.
  static const successGreen = Color(0xFF089B57);

  /// Informational accent.
  static const infoBlue = Color(0xFF5682CE);

  // ---- M3 Expressive shape scale. The last three are Expressive additions
  // absent from the SDK's bundled tokens (v6_1_0 caps at 28).
  static const rXs = 4.0;
  static const rSm = 8.0;
  static const rMd = 12.0;
  static const rLg = 16.0;
  static const rLgPlus = 20.0;   // large-increased  (M3E)
  static const rXl = 28.0;
  static const rXlPlus = 32.0;   // extra-large-increased (M3E)
  static const rXxl = 48.0;      // extra-extra-large (M3E)

  /// M3E `spring.default.spatial` -- panels, expansion, layout shifts.
  /// withDampingRatio is required: .damping is a coefficient, not a ratio.
  static final springSpatial =
      SpringDescription.withDampingRatio(mass: 1, stiffness: 380, ratio: 0.8);

  /// M3E `spring.fast.spatial` -- small elements, chips, rows.
  static final springFast =
      SpringDescription.withDampingRatio(mass: 1, stiffness: 800, ratio: 0.6);

  /// Duration equivalent of springSpatial, for APIs that take a Duration
  /// rather than a simulation (AnimatedContainer, animateTo).
  static const springSpatialDuration = Duration(milliseconds: 322);

  /// M3E `spring.default.effects` -- colour and opacity. Critically damped:
  /// never bounce a colour.
  static final springEffects =
      SpringDescription.withDampingRatio(mass: 1, stiffness: 1600, ratio: 1.0);

  // ---- compact type scale
  static const fsBody = 13.0;
  static const fsLabel = 11.0;
  static const fsMono = 12.0;
  static const fsSectionTitle = 12.0;
  static const fsPageTitle = 22.0;

  static const font = 'Roboto';
  static const mono = 'ui-monospace';

  static ThemeData light() => _build(Brightness.light);
  static ThemeData dark() => _build(Brightness.dark);

  static ThemeData _build(Brightness brightness) {
    final dark = brightness == Brightness.dark;

    var cs = ColorScheme.fromSeed(
      seedColor: brandIndigo,
      brightness: brightness,
      // Preserve readable filled actions and separate them from failure states.
      primary: dark ? const Color(0xFFB7C4FF) : brandIndigo,
      onPrimary: dark ? const Color(0xFF14245F) : Colors.white,
      tertiary: dark ? const Color(0xFFE8A33C) : attentionAmber,
      error: dark ? const Color(0xFFFF9E93) : errorCrimson,
    );

    // Near-neutral greys keep large surfaces independent of the action accent.
    cs = cs.copyWith(
      surface: dark ? const Color(0xFF16161A) : Colors.white,
      surfaceContainerLowest: dark ? const Color(0xFF101013) : Colors.white,
      surfaceContainerLow:
          dark ? const Color(0xFF1A1A1E) : const Color(0xFFFBFBFC),
      surfaceContainer: dark ? const Color(0xFF1E1E23) : const Color(0xFFF6F6F7),
      surfaceContainerHigh:
          dark ? const Color(0xFF25252B) : const Color(0xFFF0F0F2),
      surfaceContainerHighest:
          dark ? const Color(0xFF2C2C33) : const Color(0xFFE8E8EB),
      onSurface: dark ? const Color(0xFFE8E8EC) : const Color(0xFF17171B),
      onSurfaceVariant: dark ? const Color(0xFFA2A2AC) : const Color(0xFF55555E),
      outline: dark ? const Color(0xFF858590) : const Color(0xFF777780),
      outlineVariant: dark ? const Color(0xFF2A2A30) : const Color(0xFFE6E6EA),
    );

    final base = ThemeData(colorScheme: cs, useMaterial3: true);

    return base.copyWith(
      visualDensity: VisualDensity.standard,
      materialTapTargetSize: MaterialTapTargetSize.padded,
      iconButtonTheme: IconButtonThemeData(style: IconButton.styleFrom(
        minimumSize: const Size(48, 48), visualDensity: VisualDensity.standard)),
      scaffoldBackgroundColor: cs.surface,
      dividerTheme:
          DividerThemeData(color: cs.outlineVariant, space: 1, thickness: 1),

      appBarTheme: AppBarTheme(
        backgroundColor: cs.surface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        toolbarHeight: 52,
        titleTextStyle: TextStyle(
          fontFamily: font,
          color: cs.onSurface,
          fontWeight: FontWeight.w600,
          fontSize: 14,
          letterSpacing: -0.1,
        ),
        shape: Border(bottom: BorderSide(color: cs.outlineVariant)),
      ),

      cardTheme: CardThemeData(
        color: cs.surface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        margin: EdgeInsets.zero,
        // M3E: large (16) + a real superellipse rather than a circular arc.
        shape: RoundedSuperellipseBorder(
          borderRadius: BorderRadius.circular(rLg),
          side: BorderSide(color: cs.outlineVariant),
        ),
      ),

      // The one place M3E goes loud: the primary action. Stadium shape and
      // a prominent action height, with the
      // emphasized label weight. Everything else stays calm so this stands out
      // -- that contrast is the whole mechanism.
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          shape: const StadiumBorder(),
          minimumSize: const Size(0, 44),
          padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 14),
          textStyle: const TextStyle(
              fontFamily: font, fontWeight: FontWeight.w700, fontSize: 13),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          shape: const StadiumBorder(),
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          side: BorderSide(color: cs.outline),
          foregroundColor: cs.onSurface,
          textStyle: const TextStyle(
              fontFamily: font, fontWeight: FontWeight.w700, fontSize: 12),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: cs.onSurface,
          textStyle: const TextStyle(
              fontFamily: font, fontWeight: FontWeight.w700, fontSize: 12),
          shape: const StadiumBorder(),
        ),
      ),

      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: cs.surface,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(rMd),
          borderSide: BorderSide(color: cs.outline),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(rMd),
          borderSide: BorderSide(color: cs.outline),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(rMd),
          borderSide: BorderSide(color: cs.primary, width: 2),
        ),
        hintStyle: TextStyle(
            color: cs.onSurfaceVariant, fontSize: fsBody, fontFamily: font),
        contentPadding: const EdgeInsets.all(12),
        isDense: true,
      ),

      chipTheme: ChipThemeData(
        shape: const StadiumBorder(),
        side: BorderSide(color: cs.outlineVariant),
        backgroundColor: cs.surfaceContainerLow,
        labelStyle:
            TextStyle(fontSize: fsLabel, color: cs.onSurface, fontFamily: font),
        padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
      ),

      listTileTheme: ListTileThemeData(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(7)),
        selectedColor: cs.onSurface,
        iconColor: cs.onSurfaceVariant,
        minVerticalPadding: 6,
        dense: true,
      ),

      segmentedButtonTheme: SegmentedButtonThemeData(
        style: ButtonStyle(
          visualDensity: VisualDensity.standard,
          textStyle: const WidgetStatePropertyAll(TextStyle(
              fontFamily: font, fontSize: 11, fontWeight: FontWeight.w600)),
          shape: WidgetStatePropertyAll(
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(6))),
        ),
      ),

      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        backgroundColor:
            dark ? const Color(0xFF2C2C33) : const Color(0xFF1F1F23),
        contentTextStyle: const TextStyle(
            color: Colors.white, fontSize: fsBody, fontFamily: font),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      ),

      dialogTheme: DialogThemeData(
        backgroundColor: cs.surface,
        surfaceTintColor: Colors.transparent,
        shape: RoundedSuperellipseBorder(
            borderRadius: BorderRadius.circular(rXlPlus)),
      ),

      tabBarTheme: TabBarThemeData(
        labelColor: cs.onSurface,
        unselectedLabelColor: cs.onSurfaceVariant,
        labelStyle: const TextStyle(
            fontFamily: font, fontSize: 12, fontWeight: FontWeight.w600),
        unselectedLabelStyle: const TextStyle(
            fontFamily: font, fontSize: 12, fontWeight: FontWeight.w500),
        indicatorColor: cs.onSurface,
        indicatorSize: TabBarIndicatorSize.tab,
        dividerColor: cs.outlineVariant,
      ),

      tooltipTheme: TooltipThemeData(
        decoration: BoxDecoration(
          color: dark ? const Color(0xFF34343C) : const Color(0xFF25252B),
          borderRadius: BorderRadius.circular(6),
        ),
        textStyle: const TextStyle(
            color: Colors.white, fontSize: fsLabel, fontFamily: font),
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
        waitDuration: const Duration(milliseconds: 400),
      ),

      textTheme: base.textTheme.apply(fontFamily: font).copyWith(
            headlineSmall: TextStyle(
              fontFamily: font,
              fontSize: fsPageTitle,
              fontWeight: FontWeight.w700,   // M3 400 -> M3E 500; 700 for a
              letterSpacing: -0.6,           // page title in a dense console
              color: cs.onSurface,
            ),
            // M3E emphasized scale: weight only. Sizes, line-heights and
            // tracking are identical to standard M3, so this cannot reflow.
            titleMedium: TextStyle(
              fontFamily: font,
              fontSize: fsSectionTitle,
              fontWeight: FontWeight.w700,   // M3 500 -> M3E 700
              letterSpacing: 0.4,
              color: cs.onSurface,
            ),
            titleSmall: TextStyle(
              fontFamily: font,
              fontSize: 12,
              fontWeight: FontWeight.w700,   // M3 500 -> M3E 700
              color: cs.onSurface,
            ),
            bodyMedium: TextStyle(
                fontFamily: font,
                fontSize: fsBody,
                fontWeight: FontWeight.w500,  // M3 400 -> M3E 500
                color: cs.onSurface),
            labelLarge: TextStyle(
                fontFamily: font,
                fontSize: 13,
                fontWeight: FontWeight.w700,  // M3 500 -> M3E 700
                color: cs.onSurface),
            labelSmall: TextStyle(
                fontFamily: font,
                fontSize: fsLabel,
                fontWeight: FontWeight.w700,  // M3 500 -> M3E 700
                color: cs.onSurfaceVariant),
          ),
    );
  }
}

/// Semantic roles, so no widget reaches for `cs.error` to mean "heads up".
extension AdklSemantics on ColorScheme {
  Color get failure => error;
  Color get failureContainer => errorContainer;
  Color get onFailureContainer => onErrorContainer;

  Color get attention => tertiary;
  Color get attentionContainer => tertiaryContainer;
  Color get onAttentionContainer => onTertiaryContainer;

  Color get success => brightness == Brightness.dark
      ? const Color(0xFF65D99B) : const Color(0xFF007A43);
  Color get info => brightness == Brightness.dark
      ? const Color(0xFFA9C7FF) : const Color(0xFF365FA3);
}
