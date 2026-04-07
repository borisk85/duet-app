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

// Слушает authStateChanges — сам переключает между AuthScreen и MainNavigation
class AuthGate extends StatelessWidget {
  const AuthGate({super.key});

  @override
  Widget build(BuildContext context) {
    return StreamBuilder<User?>(
      stream: FirebaseAuth.instance.authStateChanges(),
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.waiting) {
          return const _SplashScreen();
        }
        if (snapshot.data == null) return const AuthScreen();
        return const MainNavigation();
      },
    );
  }
}

/// Splash-экран который показывается пока Firebase проверяет auth state.
/// Заменяет голый CircularProgressIndicator. Содержит логотип "Дуэт" с лёгкой
/// пульсацией (1.0 ↔ 1.03) и tagline. Совпадает по дизайну с native splash —
/// переход бесшовный, пользователь не видит ни моргания, ни смены кадра.
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
    // Subtle pulse 1.0 → 1.03 (по решению сеньора, не 1.05 — чтобы не было дёшево)
    _scale = Tween<double>(begin: 1.0, end: 1.03).animate(
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
              const Text('🥂', style: TextStyle(fontSize: 56)),
              const SizedBox(height: 20),
              const Text(
                'Дуэт',
                style: TextStyle(
                  color: _gold,
                  fontSize: 52,
                  fontWeight: FontWeight.w800,
                  letterSpacing: 4,
                ),
              ),
              const SizedBox(height: 12),
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
