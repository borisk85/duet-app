import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// Paywall-экран — показывается когда пользователь уперся в лимит 10 бесплатных подборок.
///
/// Триггер: ApiService.pairStream бросает PairingLimitException → ResultScreen
/// перехватывает → редиректит сюда вместо SnackBar с ошибкой.
///
/// Персонализация: показывает блюдо/напиток который пользователь хотел подобрать,
/// но не смог из-за лимита. Это превращает блокировку в момент максимальной мотивации
/// (психологически — конверсия выше когда блокировка совпадает с активным желанием).
///
/// Пока RevenueCat не подключен, кнопка "Перейти на Premium" дает только haptic.
/// После интеграции RevenueCat — открывает purchase flow.
class PaywallScreen extends StatelessWidget {
  /// То что пользователь хотел подобрать (блюдо или напиток) — для персонализации.
  /// Используется только если feature == null (контекст обычной подборки).
  final String dish;

  /// Режим в котором случилась блокировка ('food_to_alcohol' или 'alcohol_to_food').
  /// Используется только если feature == null.
  final String mode;

  /// Альтернативный контекст: блокировка фичи Premium (Expert mode, 5 категорий и т.д.)
  /// Когда задан — заголовок и описание переключаются на feature-paywall,
  /// dish/mode игнорируются. Грамматически корректно для любой Premium-фичи.
  final String? feature;

  const PaywallScreen({
    super.key,
    required this.dish,
    required this.mode,
    this.feature,
  });

  static const _gold = Color(0xFFC9A84C);
  // Более яркий золотой ТОЛЬКО для primary CTA "Перейти на Premium".
  // Основной _gold (#C9A84C) на темном фоне выглядит бежевым — для главного
  // акцента нужен насыщеннее. #E8B547 — на ~15% ярче, все еще в палитре,
  // не вульгарный желтый.
  static const _goldCta = Color(0xFFE8B547);
  static const _goldText = Color(0xFFD4B563);
  static const _bg = Color(0xFF0D0D0D);
  static const _card = Color(0xFF1A1A1A);

  @override
  Widget build(BuildContext context) {
    final isFood = mode == 'food_to_alcohol';
    final isFeature = feature != null;

    return Scaffold(
      backgroundColor: _bg,
      body: SafeArea(
        child: Stack(
          children: [
            // Кнопка закрытия в правом верхнем углу
            Positioned(
              top: 8,
              right: 8,
              child: IconButton(
                icon: Icon(Icons.close_rounded, color: Colors.white.withOpacity(0.6), size: 28),
                onPressed: () => Navigator.of(context).pop(),
              ),
            ),
            // SingleChildScrollView защищает от overflow при увеличенном
            // системном шрифте (MIUI/Android scale factor) — контент
            // скроллится если не помещается, а не ломает layout.
            // bottom 200 — больше воздуха между CTA и "Может быть позже".
            SingleChildScrollView(
              padding: const EdgeInsets.fromLTRB(24, 16, 24, 200),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const SizedBox(height: 24),
                  // Брендированный логотип Дуэт вместо generic-эмодзи —
                  // устанавливает визуальную связь "это Премиум Дуэт" и
                  // согласован с иконкой приложения.
                  Center(
                    child: Image.asset(
                      'assets/splash/duet_logo.png',
                      width: 96,
                      height: 96,
                      fit: BoxFit.contain,
                    ),
                  ),
                  const SizedBox(height: 28),
                  // Заголовок: разный для лимита подборок и блокировки фичи.
                  Text(
                    isFeature ? 'Только для Premium' : 'Бесплатный лимит исчерпан',
                    textAlign: TextAlign.center,
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 24,
                      fontWeight: FontWeight.w700,
                      letterSpacing: 0.3,
                    ),
                  ),
                  const SizedBox(height: 14),
                  // Персонализированный текст: разный для двух контекстов.
                  // 1) Лимит подборок: "Вы хотели подобрать напиток к «блюду»..."
                  // 2) Блокировка фичи: "«Экспертный режим» доступен только..."
                  RichText(
                    textAlign: TextAlign.center,
                    text: isFeature
                        ? TextSpan(
                            style: TextStyle(
                              color: Colors.white.withOpacity(0.65),
                              fontSize: 15,
                              height: 1.5,
                            ),
                            children: [
                              TextSpan(
                                text: '«${feature!}»',
                                style: const TextStyle(color: _goldText, fontWeight: FontWeight.w600),
                              ),
                              const TextSpan(
                                text: ' доступен только в Premium. Оформите подписку и получите доступ ко всем возможностям.',
                              ),
                            ],
                          )
                        : TextSpan(
                            style: TextStyle(
                              color: Colors.white.withOpacity(0.65),
                              fontSize: 15,
                              height: 1.5,
                            ),
                            children: [
                              TextSpan(text: isFood ? 'Вы хотели подобрать напиток к ' : 'Вы хотели подобрать блюда к '),
                              TextSpan(
                                text: '«$dish»',
                                style: const TextStyle(color: _goldText, fontWeight: FontWeight.w600),
                              ),
                              const TextSpan(
                                text: ' — оформите Premium и получите результат прямо сейчас.',
                              ),
                            ],
                          ),
                  ),
                  const SizedBox(height: 32),
                  // Карточка преимуществ
                  Container(
                    padding: const EdgeInsets.all(20),
                    decoration: BoxDecoration(
                      color: _card,
                      borderRadius: BorderRadius.circular(16),
                      border: Border.all(color: _gold.withOpacity(0.3), width: 1),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: const [
                            Text('⚡', style: TextStyle(fontSize: 18)),
                            SizedBox(width: 8),
                            Text(
                              'Дуэт Premium',
                              style: TextStyle(
                                color: Colors.white,
                                fontSize: 16,
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 16),
                        _buildBenefit('Безлимитные подборки'),
                        _buildBenefit('Избранное без ограничений'),
                        _buildBenefit('История подборок за месяц'),
                        _buildBenefit('Экспертный режим'),
                      ],
                    ),
                  ),
                  const SizedBox(height: 24),
                  // Цена
                  Center(
                    child: Column(
                      children: [
                        Row(
                          mainAxisAlignment: MainAxisAlignment.center,
                          crossAxisAlignment: CrossAxisAlignment.end,
                          children: [
                            const Text(
                              '\$9.99',
                              style: TextStyle(
                                color: Colors.white,
                                fontSize: 36,
                                fontWeight: FontWeight.w800,
                                letterSpacing: -0.5,
                                height: 1.0,
                              ),
                            ),
                            Padding(
                              padding: const EdgeInsets.only(bottom: 6, left: 4),
                              child: Text(
                                '/ год',
                                style: TextStyle(
                                  color: Colors.white.withOpacity(0.5),
                                  fontSize: 16,
                                  fontWeight: FontWeight.w500,
                                ),
                              ),
                            ),
                          ],
                        ),
                        // +8px воздуха чтобы "Меньше $1 в месяц" не липла к цене.
                        const SizedBox(height: 8),
                        // Якорная фраза для снятия возражения по цене —
                        // золотой цвет акцентирует выгоду, а не "примечание".
                        const Text(
                          'Меньше \$1 в месяц',
                          style: TextStyle(
                            color: _gold,
                            fontSize: 13,
                            fontWeight: FontWeight.w600,
                            height: 1.2,
                          ),
                        ),
                      ],
                    ),
                  ),
                  // Цена должна "дышать" — пользователь успевает осознать
                  // $9.99 как дешево прежде чем увидеть CTA-кнопку.
                  // Spacer убран т.к. несовместим с SingleChildScrollView;
                  // фиксированный отступ работает одинаково на всех экранах.
                  const SizedBox(height: 56),
                  // Кнопка покупки (только haptic — RevenueCat не интегрирован).
                  // Уменьшена с 56 до 50 чтобы дать больше места "Может быть
                  // позже" подняться визуально вверх от gesture exclusion zone.
                  SizedBox(
                    width: double.infinity,
                    height: 50,
                    child: ElevatedButton(
                      onPressed: () => HapticFeedback.mediumImpact(),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: _goldCta,
                        foregroundColor: _bg,
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                        elevation: 0,
                      ),
                      child: const Text(
                        'Перейти на Premium',
                        style: TextStyle(fontSize: 17, fontWeight: FontWeight.w800, letterSpacing: 0.3),
                      ),
                    ),
                  ),
                ],
              ),
            ),
            // "Может быть позже" — отдельный Positioned внизу Stack.
            // Раньше TextButton в Column все время не тапался на Xiaomi
            // несмотря на padding 60/80/100. Решение: вынести из Column
            // полностью и поставить как Positioned с фиксированным bottom 60.
            // Это полностью развязывает hit area от gesture zone и Column flow.
            // GestureDetector + HitTestBehavior.opaque гарантирует что весь
            // прямоугольник 220x48 ловит тапы (TextButton по умолчанию ловит
            // только сам текст).
            Positioned(
              left: 0,
              right: 0,
              // bottom: 56 — больше воздуха от primary CTA (было 40).
              // С GestureDetector hit area работает и в gesture exclusion zone
              // потому что Positioned развязан от Column flow и явный
              // HitTestBehavior.opaque гарантирует обработку тапов.
              bottom: 56,
              child: Center(
                child: GestureDetector(
                  behavior: HitTestBehavior.opaque,
                  onTap: () => Navigator.of(context).pop(),
                  child: Container(
                    width: 240,
                    height: 48,
                    alignment: Alignment.center,
                    child: Text(
                      'Может быть позже',
                      style: TextStyle(
                        color: Colors.white.withOpacity(0.55),
                        fontSize: 15,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildBenefit(String text) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.check_circle_rounded, color: _gold, size: 18),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              text,
              style: TextStyle(
                color: Colors.white.withOpacity(0.85),
                fontSize: 14,
                height: 1.4,
              ),
            ),
          ),
        ],
      ),
    );
  }

}
