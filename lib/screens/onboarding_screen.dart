import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Онбординг — 3 слайда при первом запуске. По спецификации сеньора:
/// 1) Что это — иконка бокала + одна строка ценности.
/// 2) Как работает — три шага с иконками.
/// 3) Персонализация — регион + до 3 предпочтений ПРЯМО на слайде.
///    Первая выдача после онбординга уже персонализирована = вау-эффект.
///
/// Пропустить можно на любом слайде через "Пропустить" в правом верхнем углу.
/// Флаг 'onboarding_done' сохраняется в SharedPreferences и проверяется
/// синхронно через initialOnboardingDone (загружено в main() до runApp).
class OnboardingScreen extends StatefulWidget {
  final VoidCallback onDone;
  const OnboardingScreen({super.key, required this.onDone});

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen>
    with SingleTickerProviderStateMixin {
  static const _gold = Color(0xFFC9A84C);
  static const _goldCta = Color(0xFFE8B547);
  static const _bg = Color(0xFF0D0D0D);
  static const _card = Color(0xFF1A1A1A);

  final PageController _controller = PageController();
  int _page = 0;

  // Третий слайд — состояние персонализации
  String _region = 'СНГ';
  final Set<String> _preferredTypes = {};

  static const _regions = ['СНГ', 'Россия', 'Казахстан', 'Украина', 'Беларусь'];
  static const _alcoholTypes = [
    {'key': 'wine', 'label': 'Вино', 'emoji': '🍷'},
    {'key': 'whisky', 'label': 'Виски', 'emoji': '🥃'},
    {'key': 'cognac', 'label': 'Коньяк', 'emoji': '🥃'},
    {'key': 'beer', 'label': 'Пиво', 'emoji': '🍺'},
    {'key': 'vodka', 'label': 'Водка', 'emoji': '🫗'},
    {'key': 'gin', 'label': 'Джин', 'emoji': '🌿'},
    {'key': 'rum', 'label': 'Ром', 'emoji': '🍹'},
    {'key': 'tequila', 'label': 'Текила', 'emoji': '🌵'},
    {'key': 'cocktails', 'label': 'Коктейли', 'emoji': '🍸'},
    {'key': 'sparkling', 'label': 'Игристое', 'emoji': '🥂'},
  ];

  late final AnimationController _shakeController;
  late final Animation<double> _shakeAnimation;

  @override
  void initState() {
    super.initState();
    _shakeController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 400),
    );
    _shakeAnimation = TweenSequence<double>([
      TweenSequenceItem(tween: Tween(begin: 0.0, end: -8.0), weight: 1),
      TweenSequenceItem(tween: Tween(begin: -8.0, end: 8.0), weight: 2),
      TweenSequenceItem(tween: Tween(begin: 8.0, end: -6.0), weight: 2),
      TweenSequenceItem(tween: Tween(begin: -6.0, end: 0.0), weight: 1),
    ]).animate(_shakeController);
  }

  @override
  void dispose() {
    _controller.dispose();
    _shakeController.dispose();
    super.dispose();
  }

  Future<void> _finish() async {
    HapticFeedback.lightImpact();
    final prefs = await SharedPreferences.getInstance();
    // Сохраняем регион и предпочтения если пользователь их выбрал на слайде 3.
    // Если пропустил онбординг раньше — сохраняются дефолты ('СНГ', пусто).
    await prefs.setString('region', _region);
    await prefs.setStringList('preferred_types', _preferredTypes.toList());
    await prefs.setBool('onboarding_done', true);
    if (mounted) widget.onDone();
  }

  void _next() {
    HapticFeedback.lightImpact();
    if (_page < 2) {
      _controller.nextPage(
        duration: const Duration(milliseconds: 280),
        curve: Curves.easeOut,
      );
    } else {
      _finish();
    }
  }

  void _togglePreference(String key) {
    HapticFeedback.lightImpact();
    if (_preferredTypes.contains(key)) {
      setState(() => _preferredTypes.remove(key));
    } else if (_preferredTypes.length < 3) {
      setState(() => _preferredTypes.add(key));
    } else {
      // 4-й тап — shake-анимация без SnackBar (как в profile_screen)
      _shakeController.forward(from: 0);
    }
  }

  @override
  Widget build(BuildContext context) {
    final isLast = _page == 2;
    return Scaffold(
      backgroundColor: _bg,
      body: SafeArea(
        child: Column(
          children: [
            // Кнопка Пропустить — правый верхний угол. Доступна на ВСЕХ слайдах
            // включая последний — пользователь не должен быть заперт.
            Align(
              alignment: Alignment.topRight,
              child: TextButton(
                onPressed: _finish,
                child: Text(
                  'Пропустить',
                  style: TextStyle(
                    color: Colors.white.withOpacity(0.5),
                    fontSize: 14,
                    fontWeight: FontWeight.w500,
                  ),
                ),
              ),
            ),
            Expanded(
              child: PageView(
                controller: _controller,
                onPageChanged: (i) => setState(() => _page = i),
                children: [
                  _buildSlide1(),
                  _buildSlide2(),
                  _buildSlide3(),
                ],
              ),
            ),
            // Индикаторы страниц (золотые точки, активная вытянута)
            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: List.generate(3, (i) {
                final selected = i == _page;
                return AnimatedContainer(
                  duration: const Duration(milliseconds: 220),
                  margin: const EdgeInsets.symmetric(horizontal: 4),
                  width: selected ? 24 : 8,
                  height: 8,
                  decoration: BoxDecoration(
                    color: selected ? _gold : Colors.white.withOpacity(0.2),
                    borderRadius: BorderRadius.circular(4),
                  ),
                );
              }),
            ),
            const SizedBox(height: 28),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 24),
              child: SizedBox(
                width: double.infinity,
                height: 54,
                child: ElevatedButton(
                  onPressed: _next,
                  style: ElevatedButton.styleFrom(
                    backgroundColor: _goldCta,
                    foregroundColor: _bg,
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                    elevation: 0,
                  ),
                  child: Text(
                    isLast ? 'Начать' : 'Далее',
                    style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w800, letterSpacing: 0.3),
                  ),
                ),
              ),
            ),
            const SizedBox(height: 36),
          ],
        ),
      ),
    );
  }

  // Слайд 1 — что это
  Widget _buildSlide1() {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 32),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Container(
            width: 140,
            height: 140,
            decoration: BoxDecoration(
              color: _gold.withOpacity(0.10),
              shape: BoxShape.circle,
              border: Border.all(color: _gold.withOpacity(0.4), width: 2),
            ),
            child: const Center(
              child: Text('🥂', style: TextStyle(fontSize: 64)),
            ),
          ),
          const SizedBox(height: 40),
          const Text(
            'Подберём напиток к любому блюду',
            textAlign: TextAlign.center,
            style: TextStyle(
              color: Colors.white,
              fontSize: 24,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.3,
              height: 1.3,
            ),
          ),
          const SizedBox(height: 16),
          Text(
            'Введите блюдо — получите идеальное сочетание за секунды.',
            textAlign: TextAlign.center,
            style: TextStyle(
              color: Colors.white.withOpacity(0.65),
              fontSize: 15,
              height: 1.5,
            ),
          ),
        ],
      ),
    );
  }

  // Слайд 2 — как работает
  Widget _buildSlide2() {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 32),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Text(
            'Как это работает',
            textAlign: TextAlign.center,
            style: TextStyle(
              color: Colors.white,
              fontSize: 24,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.3,
            ),
          ),
          const SizedBox(height: 36),
          _buildStep('1', '🍽️', 'Введите блюдо или напиток'),
          const SizedBox(height: 18),
          _buildStep('2', '✨', 'AI подбирает тройку лучших вариантов'),
          const SizedBox(height: 18),
          _buildStep('3', '⭐', 'Сохраняйте и делитесь'),
        ],
      ),
    );
  }

  Widget _buildStep(String num, String emoji, String text) {
    return Row(
      children: [
        Container(
          width: 40,
          height: 40,
          decoration: BoxDecoration(
            color: _gold.withOpacity(0.12),
            shape: BoxShape.circle,
            border: Border.all(color: _gold.withOpacity(0.4), width: 1.5),
          ),
          child: Center(child: Text(emoji, style: const TextStyle(fontSize: 20))),
        ),
        const SizedBox(width: 16),
        Expanded(
          child: Text(
            text,
            style: const TextStyle(
              color: Colors.white,
              fontSize: 16,
              fontWeight: FontWeight.w500,
              height: 1.4,
            ),
          ),
        ),
      ],
    );
  }

  // Слайд 3 — персонализация прямо здесь
  Widget _buildSlide3() {
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(24, 8, 24, 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Настройте под себя',
            textAlign: TextAlign.center,
            style: TextStyle(
              color: Colors.white,
              fontSize: 24,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.3,
            ),
          ),
          const SizedBox(height: 8),
          Center(
            child: Text(
              'Первая подборка уже будет персональной',
              textAlign: TextAlign.center,
              style: TextStyle(
                color: Colors.white.withOpacity(0.55),
                fontSize: 13,
              ),
            ),
          ),
          const SizedBox(height: 28),
          Text(
            'РЕГИОН',
            style: TextStyle(
              color: Colors.white.withOpacity(0.4),
              fontSize: 11,
              fontWeight: FontWeight.w700,
              letterSpacing: 1.2,
            ),
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: _regions.map((r) {
              final selected = _region == r;
              return GestureDetector(
                onTap: () {
                  HapticFeedback.lightImpact();
                  setState(() => _region = r);
                },
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                  decoration: BoxDecoration(
                    color: selected ? _gold.withOpacity(0.15) : _card,
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(
                      color: selected ? _gold : Colors.white.withOpacity(0.08),
                      width: selected ? 1.5 : 1,
                    ),
                  ),
                  child: Text(
                    r,
                    style: TextStyle(
                      color: selected ? _gold : Colors.white.withOpacity(0.75),
                      fontSize: 13,
                      fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
                    ),
                  ),
                ),
              );
            }).toList(),
          ),
          const SizedBox(height: 28),
          Row(
            children: [
              Text(
                'ЧТО ЛЮБИТЕ ПИТЬ?',
                style: TextStyle(
                  color: Colors.white.withOpacity(0.4),
                  fontSize: 11,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 1.2,
                ),
              ),
              const SizedBox(width: 8),
              Text(
                '(необязательно)',
                style: TextStyle(
                  color: Colors.white.withOpacity(0.3),
                  fontSize: 11,
                  fontWeight: FontWeight.w400,
                  fontStyle: FontStyle.italic,
                ),
              ),
              const Spacer(),
              Text(
                '${_preferredTypes.length} / 3',
                style: TextStyle(
                  color: Colors.white.withOpacity(0.4),
                  fontSize: 11,
                  fontWeight: FontWeight.w500,
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          AnimatedBuilder(
            animation: _shakeAnimation,
            builder: (_, child) => Transform.translate(
              offset: Offset(_shakeAnimation.value, 0),
              child: child,
            ),
            child: Wrap(
              spacing: 8,
              runSpacing: 8,
              children: _alcoholTypes.map((t) {
                final key = t['key']!;
                final selected = _preferredTypes.contains(key);
                return GestureDetector(
                  onTap: () => _togglePreference(key),
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    decoration: BoxDecoration(
                      color: selected ? _gold.withOpacity(0.15) : _card,
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(
                        color: selected ? _gold : Colors.white.withOpacity(0.08),
                        width: selected ? 1.5 : 1,
                      ),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(t['emoji']!, style: const TextStyle(fontSize: 14)),
                        const SizedBox(width: 6),
                        Text(
                          t['label']!,
                          style: TextStyle(
                            color: selected ? _gold : Colors.white.withOpacity(0.75),
                            fontSize: 13,
                            fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
                          ),
                        ),
                      ],
                    ),
                  ),
                );
              }).toList(),
            ),
          ),
        ],
      ),
    );
  }
}
