import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:google_fonts/google_fonts.dart';
import 'screens/home_screen.dart';
import 'screens/conversation_screen.dart';
import 'screens/memory_screen.dart';
import 'screens/faces_screen.dart';
import 'screens/settings_screen.dart';
import 'screens/caregiver_screen.dart';

final _router = GoRouter(
  initialLocation: '/',
  routes: [
    GoRoute(
      path: '/',
      builder: (context, state) => const HomeScreen(),
    ),
    GoRoute(
      path: '/conversation',
      builder: (context, state) => const ConversationScreen(),
    ),
    GoRoute(
      path: '/memory',
      builder: (context, state) => const MemoryScreen(),
    ),
    GoRoute(
      path: '/faces',
      builder: (context, state) => const FacesScreen(),
    ),
    GoRoute(
      path: '/settings',
      builder: (context, state) => const SettingsScreen(),
    ),
    GoRoute(
      path: '/caregiver',
      builder: (context, state) => const CaregiverScreen(),
    ),
  ],
);

class SahayakApp extends ConsumerWidget {
  const SahayakApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return MaterialApp.router(
      title: 'Sahayak',
      debugShowCheckedModeBanner: false,
      theme: _buildTheme(),
      routerConfig: _router,
    );
  }

  ThemeData _buildTheme() {
    const primaryOrange = Color(0xFFE65100);
    const secondaryAmber = Color(0xFFFFB300);
    const backgroundWarm = Color(0xFFFFF8F0);
    const surfaceWarm = Color(0xFFFFF3E0);

    final baseTextTheme = GoogleFonts.notoSansTextTheme();

    return ThemeData(
      useMaterial3: true,
      colorScheme: ColorScheme.fromSeed(
        seedColor: primaryOrange,
        primary: primaryOrange,
        secondary: secondaryAmber,
        surface: surfaceWarm,
        background: backgroundWarm,
        brightness: Brightness.light,
      ),
      scaffoldBackgroundColor: backgroundWarm,
      appBarTheme: AppBarTheme(
        backgroundColor: primaryOrange,
        foregroundColor: Colors.white,
        elevation: 2,
        titleTextStyle: GoogleFonts.notoSans(
          fontSize: 24,
          fontWeight: FontWeight.w700,
          color: Colors.white,
        ),
      ),
      cardTheme: CardTheme(
        color: surfaceWarm,
        elevation: 3,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
        ),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: primaryOrange,
          foregroundColor: Colors.white,
          padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 16),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(14),
          ),
          textStyle: GoogleFonts.notoSans(
            fontSize: 20,
            fontWeight: FontWeight.w600,
          ),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: primaryOrange,
          textStyle: GoogleFonts.notoSans(
            fontSize: 18,
            fontWeight: FontWeight.w600,
          ),
        ),
      ),
      chipTheme: ChipThemeData(
        backgroundColor: surfaceWarm,
        selectedColor: secondaryAmber,
        labelStyle: GoogleFonts.notoSans(fontSize: 16),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      ),
      inputDecorationTheme: InputDecorationTheme(
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: primaryOrange),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: primaryOrange, width: 2),
        ),
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
        labelStyle: GoogleFonts.notoSans(fontSize: 18),
        hintStyle: GoogleFonts.notoSans(fontSize: 18, color: Colors.grey),
      ),
      textTheme: baseTextTheme.copyWith(
        displayLarge: baseTextTheme.displayLarge?.copyWith(
          fontSize: 61,
          fontWeight: FontWeight.w700,
          color: Colors.black87,
        ),
        displayMedium: baseTextTheme.displayMedium?.copyWith(
          fontSize: 49,
          fontWeight: FontWeight.w700,
          color: Colors.black87,
        ),
        displaySmall: baseTextTheme.displaySmall?.copyWith(
          fontSize: 41,
          fontWeight: FontWeight.w600,
          color: Colors.black87,
        ),
        headlineLarge: baseTextTheme.headlineLarge?.copyWith(
          fontSize: 36,
          fontWeight: FontWeight.w700,
          color: Colors.black87,
        ),
        headlineMedium: baseTextTheme.headlineMedium?.copyWith(
          fontSize: 32,
          fontWeight: FontWeight.w600,
          color: Colors.black87,
        ),
        headlineSmall: baseTextTheme.headlineSmall?.copyWith(
          fontSize: 28,
          fontWeight: FontWeight.w600,
          color: Colors.black87,
        ),
        titleLarge: baseTextTheme.titleLarge?.copyWith(
          fontSize: 26,
          fontWeight: FontWeight.w700,
          color: Colors.black87,
        ),
        titleMedium: baseTextTheme.titleMedium?.copyWith(
          fontSize: 22,
          fontWeight: FontWeight.w600,
          color: Colors.black87,
        ),
        titleSmall: baseTextTheme.titleSmall?.copyWith(
          fontSize: 18,
          fontWeight: FontWeight.w600,
          color: Colors.black87,
        ),
        bodyLarge: baseTextTheme.bodyLarge?.copyWith(
          fontSize: 22,
          color: Colors.black87,
        ),
        bodyMedium: baseTextTheme.bodyMedium?.copyWith(
          fontSize: 18,
          color: Colors.black87,
        ),
        bodySmall: baseTextTheme.bodySmall?.copyWith(
          fontSize: 16,
          color: Colors.black54,
        ),
        labelLarge: baseTextTheme.labelLarge?.copyWith(
          fontSize: 20,
          fontWeight: FontWeight.w600,
          color: Colors.black87,
        ),
        labelMedium: baseTextTheme.labelMedium?.copyWith(
          fontSize: 17,
          color: Colors.black87,
        ),
        labelSmall: baseTextTheme.labelSmall?.copyWith(
          fontSize: 15,
          color: Colors.black54,
        ),
      ),
      bottomNavigationBarTheme: BottomNavigationBarThemeData(
        backgroundColor: Colors.white,
        selectedItemColor: primaryOrange,
        unselectedItemColor: Colors.grey,
        selectedLabelStyle: GoogleFonts.notoSans(
          fontSize: 15,
          fontWeight: FontWeight.w600,
        ),
        unselectedLabelStyle: GoogleFonts.notoSans(fontSize: 14),
        type: BottomNavigationBarType.fixed,
        elevation: 8,
      ),
      floatingActionButtonTheme: const FloatingActionButtonThemeData(
        backgroundColor: primaryOrange,
        foregroundColor: Colors.white,
        elevation: 6,
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: Colors.black87,
        contentTextStyle: GoogleFonts.notoSans(
          fontSize: 18,
          color: Colors.white,
        ),
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
        ),
      ),
      dialogTheme: DialogTheme(
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(20),
        ),
        titleTextStyle: GoogleFonts.notoSans(
          fontSize: 24,
          fontWeight: FontWeight.w700,
          color: Colors.black87,
        ),
        contentTextStyle: GoogleFonts.notoSans(
          fontSize: 18,
          color: Colors.black87,
        ),
      ),
    );
  }
}
