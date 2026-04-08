import 'dart:async';

import 'package:firebase_auth/firebase_auth.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'firebase_options.dart';
import 'screens/auth_screen.dart';
import 'screens/home_screen.dart';
import 'screens/favorites_screen.dart';
import 'screens/history_screen.dart';
import 'screens/profile_screen.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setSystemUIOverlayStyle(
    const SystemUiOverlayStyle(
      statusBarColor: Colors.transparent,
      statusBarIconBrightness: Brightness.light,
    ),
  );
  await Firebase.initializeApp(
    options: DefaultFirebaseOptions.currentPlatform,
  );
  runApp(const PairingApp());
}

class PairingApp extends StatelessWidget {
  const PairingApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Дуэт',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFFC9A84C),
          brightness: Brightness.dark,
        ),
        useMaterial3: true,
      ),
      home: const AuthGate(),
    );
  }
}

// Слушает authStateChanges и гарантирует минимальное время показа splash'а.
// Даже если Firebase резолвит auth мгновенно (закешированный токен),
// splash висит минимум _minSplashMs — пользователь успевает прочитать название
// и ощутить бренд. Без этого splash мелькает на 100-200мс и никто его не видит.
class AuthGate extends StatefulWidget {
  const AuthGate({super.key});

  @override
  State<AuthGate> createState() => _AuthGateState();
}

class _AuthGateState extends State<AuthGate> {
  static const _minSplashMs = 1800;

  bool _minElapsed = false;
  bool _authReady = false;
  User? _user;
  StreamSubscription<User?>? _authSub;

  @override
  void initState() {
    super.initState();
    Future.delayed(const Duration(milliseconds: _minSplashMs), () {
      if (mounted) setState(() => _minElapsed = true);
    });
    _authSub = FirebaseAuth.instance.authStateChanges().listen((user) {
      if (!mounted) return;
      setState(() {
        _user = user;
        _authReady = true;
      });
    });
  }

  @override
  void dispose() {
    _authSub?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!_minElapsed || !_authReady) return const _SplashScreen();
    if (_user == null) return const AuthScreen();
    return const MainNavigation();
  }
}

/// Splash-экран который показывается пока Firebase проверяет auth state,
/// минимум 1800мс (см. AuthGate._minSplashMs) чтобы пользователь успел прочитать
/// название и ощутить бренд. Содержит логотип "Дуэт" с лёгкой пульсацией
/// (1.0 ↔ 1.05 — на физическом экране 1.03 глаз не цепляет) и tagline.
/// Совпадает по дизайну с native splash — переход бесшовный.
class _SplashScreen extends StatefulWidget {
  const _SplashScreen();

  @override
  State<_SplashScreen> createState() => _SplashScreenState();
}

class _SplashScreenState extends State<_SplashScreen>
    with SingleTickerProviderStateMixin {
  static const _gold = Color(0xFFC9A84C);
  static const _bg = Color(0xFF0D0D0D);

  late final AnimationController _pulse;
  late final Animation<double> _scale;

  @override
  void initState() {
    super.initState();
    _pulse = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1500),
    )..repeat(reverse: true);
    // Pulse 1.0 → 1.05: на физическом экране 1.03 не цепляет глаз,
    // 1.05 — граница между "не видно" и "дёшево", правильная для реального устройства.
    _scale = Tween<double>(begin: 1.0, end: 1.05).animate(
      CurvedAnimation(parent: _pulse, curve: Curves.easeInOut),
    );
  }

  @override
  void dispose() {
    _pulse.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _bg,
      body: Center(
        child: ScaleTransition(
          scale: _scale,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text(
                'Дуэт',
                style: TextStyle(
                  color: _gold,
                  fontSize: 56,
                  fontWeight: FontWeight.w800,
                  letterSpacing: 4,
                ),
              ),
              const SizedBox(height: 14),
              Text(
                'AI-эксперт по напиткам к еде',
                style: TextStyle(
                  color: Colors.white.withOpacity(0.4),
                  fontSize: 14,
                  letterSpacing: 0.5,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class MainNavigation extends StatefulWidget {
  const MainNavigation({super.key});

  @override
  State<MainNavigation> createState() => _MainNavigationState();
}

class _MainNavigationState extends State<MainNavigation> {
  int _currentIndex = 0;
  int _favoritesEpoch = 0;
  int _historyEpoch = 0;
  int _profileEpoch = 0;

  static const _gold = Color(0xFFC9A84C);
  static const _bg = Color(0xFF0D0D0D);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _bg,
      body: IndexedStack(
        index: _currentIndex,
        children: [
          const HomeScreen(),
          FavoritesScreen(key: ValueKey(_favoritesEpoch), onGoHome: () => setState(() => _currentIndex = 0)),
          HistoryScreen(key: ValueKey(_historyEpoch)),
          ProfileScreen(key: ValueKey(_profileEpoch)),
        ],
      ),
      bottomNavigationBar: _buildBottomNav(),
    );
  }

  Widget _buildBottomNav() {
    return Container(
      decoration: BoxDecoration(
        color: const Color(0xFF111111),
        border: Border(
          top: BorderSide(color: Colors.white.withOpacity(0.07), width: 1),
        ),
      ),
      child: SafeArea(
        child: SizedBox(
          height: 60,
          child: Row(
            children: [
              _navItem(index: 0, icon: Icons.search_rounded, label: 'Подбор'),
              _navItem(index: 1, icon: Icons.star_rounded, label: 'Избранное'),
              _navItem(index: 2, icon: Icons.history_rounded, label: 'История'),
              _navItem(index: 3, icon: Icons.person_rounded, label: 'Профиль'),
            ],
          ),
        ),
      ),
    );
  }

  Widget _navItem({required int index, required IconData icon, required String label}) {
    final selected = _currentIndex == index;
    return Expanded(
      child: GestureDetector(
        onTap: () {
          HapticFeedback.lightImpact();
          setState(() {
            if (index == 1) _favoritesEpoch++;
            if (index == 2) _historyEpoch++;
            if (index == 3) _profileEpoch++;
            _currentIndex = index;
          });
        },
        behavior: HitTestBehavior.opaque,
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            AnimatedContainer(
              duration: const Duration(milliseconds: 200),
              child: Icon(
                icon,
                size: 24,
                color: selected ? _gold : Colors.white.withOpacity(0.55),
              ),
            ),
            const SizedBox(height: 4),
            Text(
              label,
              style: TextStyle(
                fontSize: 11,
                color: selected ? _gold : Colors.white.withOpacity(0.55),
                fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
