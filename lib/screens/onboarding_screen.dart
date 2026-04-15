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
  String _region = 'Другая страна';
  final Set<String> _preferredTypes = {};

  // Расширенный список стран СНГ+ чтобы пользователь из Узбекистана/
  // Кыргызстана/Армении сразу видел свою страну. "Другое" в конце — для всех
  // кто вне СНГ. На бэке REGION_AVAILABILITY неизвестные регионы и "Другое"
  // уходят в default (универсальный СНГ-список брендов).
  static const _regions = [
    'Россия', 'Казахстан', 'Украина', 'Беларусь',
    'Узбекистан', 'Кыргызстан', 'Таджикистан', 'Туркменистан',
    'Армения', 'Азербайджан', 'Грузия', 'Молдова',
    'Другая страна',
  ];
  // Ключи строго совпадают с profile_screen.dart _alcoholTypes — иначе
  // выбор в онбординге не отображается потом в профиле как выбранный.
  // Это shared SharedPreferences key 'preferred_types', сравнение по
  // строкам. Раньше был баг: 'whisky' в онбординге vs 'whiskey' в профиле.
  static const _alcoholTypes = [
    {'key': 'wine', 'label': 'Вино', 'emoji': '🍷'},
    {'key': 'whiskey', 'label': 'Виски', 'emoji': '🥃'},
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
  void didChangeDependencies() {
    super.didChangeDependencies();
    // Precache логотип слайда 1 — без этого первый декодинг PNG происходит
    // в момент transition 1→2 и даёт frame drop. С precache картинка уже
    // в GPU-памяти к моменту показа.
    precacheImage(
      const AssetImage('assets/splash/duet_logo_transparent.png'),
      context,
    );
  }

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
            // +12px top padding от system bar (было 0, кнопка липла к часам).
            // opacity 0.65 вместо 0.5 — контраст лучше, остаётся вторичной.
            Padding(
              padding: const EdgeInsets.only(top: 12, right: 12),
              child: Align(
                alignment: Alignment.topRight,
                child: TextButton(
                  onPressed: _finish,
                  child: Text(
                    'Пропустить',
                    style: TextStyle(
                      color: Colors.white.withOpacity(0.65),
                      fontSize: 14,
                      fontWeight: FontWeight.w500,
                    ),
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

  // Слайд 1 — Hero. Cal AI стандарт: иконка + 1 жирная строка + 1 тонкая.
  // Никаких объяснений как работает приложение.
  Widget _buildSlide1() {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 32),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          // Брендированный логотип Дуэт с прозрачным фоном (берем
          // foreground adaptive-icon, у него alpha=0 в углах). Прежний
          // duet_logo.png был непрозрачным PNG — давал квадратик на фоне.
          Image.asset(
            'assets/splash/duet_logo_transparent.png',
            width: 160,
            height: 160,
            fit: BoxFit.contain,
          ),
          const SizedBox(height: 48),
          const Text(
            'Идеальный напиток\nк любому блюду',
            textAlign: TextAlign.center,
            style: TextStyle(
              color: Colors.white,
              fontSize: 28,
              fontWeight: FontWeight.w800,
              letterSpacing: 0.3,
              height: 1.25,
            ),
          ),
          const SizedBox(height: 14),
          Text(
            'AI подберет напиток за секунды',
            textAlign: TextAlign.center,
            style: TextStyle(
              color: Colors.white.withOpacity(0.55),
              fontSize: 15,
              height: 1.5,
            ),
          ),
        ],
      ),
    );
  }

  // Слайд 2 — Регион. Один вопрос, чипсы, кнопка Далее.
  // Cal AI паттерн: каждый слайд = одно действие.
  Widget _buildSlide2() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(24, 24, 24, 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.center,
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Container(
            width: 96,
            height: 96,
            decoration: BoxDecoration(
              color: _gold.withOpacity(0.10),
              shape: BoxShape.circle,
              border: Border.all(color: _gold.withOpacity(0.4), width: 2),
            ),
            child: const Center(
              child: Text('🌍', style: TextStyle(fontSize: 44)),
            ),
          ),
          const SizedBox(height: 32),
          const Text(
            'Откуда вы?',
            textAlign: TextAlign.center,
            style: TextStyle(
              color: Colors.white,
              fontSize: 26,
              fontWeight: FontWeight.w800,
              letterSpacing: 0.3,
            ),
          ),
          const SizedBox(height: 10),
          Text(
            'Чтобы предлагать доступные бренды',
            textAlign: TextAlign.center,
            style: TextStyle(
              color: Colors.white.withOpacity(0.55),
              fontSize: 14,
            ),
          ),
          const SizedBox(height: 32),
          // Компактные чипы для 12 регионов — padding 14x8 и fontSize 13
          // вместо 18x12/15, чтобы весь список влез без скролла на 1080x2400.
          Wrap(
            spacing: 8,
            runSpacing: 8,
            alignment: WrapAlignment.center,
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
                      color: selected ? _gold : Colors.white.withOpacity(0.85),
                      fontSize: 13,
                      fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
                    ),
                  ),
                ),
              );
            }).toList(),
          ),
        ],
      ),
    );
  }

  // Слайд 3 — Предпочтения. Опционально, без давления.
  Widget _buildSlide3() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(24, 24, 24, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.center,
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Container(
            width: 96,
            height: 96,
            decoration: BoxDecoration(
              color: _gold.withOpacity(0.10),
              shape: BoxShape.circle,
              border: Border.all(color: _gold.withOpacity(0.4), width: 2),
            ),
            child: const Center(
              child: Text('🍷', style: TextStyle(fontSize: 44)),
            ),
          ),
          const SizedBox(height: 28),
          const Text(
            'Ваши любимые напитки?',
            textAlign: TextAlign.center,
            style: TextStyle(
              color: Colors.white,
              fontSize: 26,
              fontWeight: FontWeight.w800,
              letterSpacing: 0.3,
            ),
          ),
          const SizedBox(height: 10),
          Text(
            'Выберите до 3 — или пропустите',
            textAlign: TextAlign.center,
            style: TextStyle(
              color: Colors.white.withOpacity(0.55),
              fontSize: 14,
            ),
          ),
          const SizedBox(height: 24),
          AnimatedBuilder(
            animation: _shakeAnimation,
            builder: (_, child) => Transform.translate(
              offset: Offset(_shakeAnimation.value, 0),
              child: child,
            ),
            child: Wrap(
              spacing: 8,
              runSpacing: 8,
              alignment: WrapAlignment.center,
              children: _alcoholTypes.map((t) {
                final key = t['key']!;
                final selected = _preferredTypes.contains(key);
                return GestureDetector(
                  onTap: () => _togglePreference(key),
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 9),
                    decoration: BoxDecoration(
                      color: selected ? _gold.withOpacity(0.15) : _card,
                      borderRadius: BorderRadius.circular(11),
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
                            color: selected ? _gold : Colors.white.withOpacity(0.85),
                            fontSize: 13,
                            fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
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
