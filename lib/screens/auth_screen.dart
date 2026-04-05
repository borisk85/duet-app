import 'package:flutter/material.dart';
import '../services/auth_service.dart';

class AuthScreen extends StatefulWidget {
  const AuthScreen({super.key});

  @override
  State<AuthScreen> createState() => _AuthScreenState();
}

class _AuthScreenState extends State<AuthScreen> {
  static const _gold = Color(0xFFC9A84C);
  static const _bg = Color(0xFF0D0D0D);
  static const _card = Color(0xFF1A1A1A);

  bool _loadingGoogle = false;
  bool _loadingAnon = false;

  Future<void> _signInWithGoogle() async {
    setState(() => _loadingGoogle = true);
    try {
      await AuthService.signInWithGoogle();
      // authStateChanges в main.dart сам переключит на главный экран
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Ошибка входа: ${e.toString().replaceAll('Exception: ', '')}'),
            backgroundColor: Colors.red.shade800,
            behavior: SnackBarBehavior.floating,
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _loadingGoogle = false);
    }
  }

  Future<void> _continueAnonymously() async {
    setState(() => _loadingAnon = true);
    try {
      await AuthService.signInAnonymously();
    } finally {
      if (mounted) setState(() => _loadingAnon = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _bg,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 32),
          child: Column(
            children: [
              const Spacer(flex: 2),
              _buildLogo(),
              const Spacer(flex: 3),
              _buildGoogleButton(),
              const SizedBox(height: 16),
              _buildAnonButton(),
              const Spacer(flex: 1),
              _buildDisclaimer(),
              const SizedBox(height: 24),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildLogo() {
    return Column(
      children: [
        const Text(
          'Дуэт',
          style: TextStyle(
            color: _gold,
            fontSize: 48,
            fontWeight: FontWeight.w700,
            letterSpacing: 3,
          ),
        ),
        const SizedBox(height: 12),
        Text(
          'Персональный сомелье',
          style: TextStyle(
            color: Colors.white.withOpacity(0.45),
            fontSize: 16,
            letterSpacing: 0.5,
          ),
        ),
        const SizedBox(height: 8),
        Text(
          'Подберем напиток к любому блюду',
          style: TextStyle(
            color: Colors.white.withOpacity(0.25),
            fontSize: 13,
          ),
        ),
      ],
    );
  }

  Widget _buildGoogleButton() {
    return SizedBox(
      width: double.infinity,
      height: 54,
      child: ElevatedButton(
        onPressed: (_loadingGoogle || _loadingAnon) ? null : _signInWithGoogle,
        style: ElevatedButton.styleFrom(
          backgroundColor: Colors.white,
          foregroundColor: Colors.black87,
          disabledBackgroundColor: Colors.white.withOpacity(0.5),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
          elevation: 0,
        ),
        child: _loadingGoogle
            ? const SizedBox(
                width: 22,
                height: 22,
                child: CircularProgressIndicator(strokeWidth: 2.5, color: Colors.black54),
              )
            : Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  _googleLogo(),
                  const SizedBox(width: 12),
                  const Text(
                    'Войти через Google',
                    style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
                  ),
                ],
              ),
      ),
    );
  }

  Widget _googleLogo() {
    return Container(
      width: 22,
      height: 22,
      decoration: const BoxDecoration(shape: BoxShape.circle),
      child: const Text(
        'G',
        textAlign: TextAlign.center,
        style: TextStyle(
          fontSize: 16,
          fontWeight: FontWeight.w700,
          color: Color(0xFF4285F4),
        ),
      ),
    );
  }

  Widget _buildAnonButton() {
    return SizedBox(
      width: double.infinity,
      height: 54,
      child: ElevatedButton(
        onPressed: (_loadingGoogle || _loadingAnon) ? null : _continueAnonymously,
        style: ElevatedButton.styleFrom(
          backgroundColor: _card,
          foregroundColor: Colors.white,
          disabledBackgroundColor: _card.withOpacity(0.5),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(14),
            side: BorderSide(color: Colors.white.withOpacity(0.08)),
          ),
          elevation: 0,
        ),
        child: _loadingAnon
            ? const SizedBox(
                width: 22,
                height: 22,
                child: CircularProgressIndicator(strokeWidth: 2.5, color: Colors.white54),
              )
            : Text(
                'Продолжить без аккаунта',
                style: TextStyle(
                  fontSize: 15,
                  color: Colors.white.withOpacity(0.6),
                ),
              ),
      ),
    );
  }

  Widget _buildDisclaimer() {
    return Text(
      'Без аккаунта история и избранное\nне сохраняются при переустановке',
      textAlign: TextAlign.center,
      style: TextStyle(
        color: Colors.white.withOpacity(0.22),
        fontSize: 12,
        height: 1.6,
      ),
    );
  }
}
