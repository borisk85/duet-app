import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../main.dart' show initialBudgetKey;
import 'result_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final TextEditingController _controller = TextEditingController();
  bool _isFoodToAlcohol = true;
  // Инициализируем синхронно из top-level initialBudgetKey (прогружено в main()
  // до runApp) — без мелькания с дефолта "Средний" на сохраненное значение.
  late int _budgetIndex = _initialBudgetIndex();
  bool _navigating = false;

  static const _budgetLabels = ['Бюджетно', 'Средний', 'Премиум'];
  static const _budgetIcons = ['💰', '💰💰', '💰💰💰'];
  static const _budgetKeys = ['budget', 'medium', 'premium'];

  static const _gold = Color(0xFFC9A84C);
  static const _bg = Color(0xFF0D0D0D);
  static const _card = Color(0xFF1A1A1A);

  int _initialBudgetIndex() {
    final idx = _budgetKeys.indexOf(initialBudgetKey);
    return idx == -1 ? 1 : idx;
  }

  Future<void> _saveBudget(int idx) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('budget', _budgetKeys[idx]);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _bg,
      body: SafeArea(
        child: Column(
          children: [
            Expanded(
              child: SingleChildScrollView(
                padding: const EdgeInsets.symmetric(horizontal: 24.0),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const SizedBox(height: 40),
                    _buildLogo(),
                    const SizedBox(height: 32),
                    _buildToggle(),
                    const SizedBox(height: 28),
                    _buildLabel(),
                    const SizedBox(height: 12),
                    _buildInputField(),
                    const SizedBox(height: 12),
                    _buildHints(),
                    if (_isFoodToAlcohol) ...[
                      const SizedBox(height: 24),
                      _buildBudgetSelector(),
                    ],
                    const SizedBox(height: 24),
                  ],
                ),
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(24, 0, 24, 36),
              child: _buildButton(),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildLogo() {
    return const Text(
      'Дуэт',
      style: TextStyle(
        color: _gold,
        fontSize: 28,
        fontWeight: FontWeight.w700,
        letterSpacing: 1.5,
      ),
    );
  }

  Widget _buildToggle() {
    return Container(
      height: 48,
      decoration: BoxDecoration(
        color: _card,
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(
        children: [
          _toggleOption(
            label: 'Еда → Напиток',
            selected: _isFoodToAlcohol,
            onTap: () => setState(() => _isFoodToAlcohol = true),
          ),
          _toggleOption(
            label: 'Напиток → Еда',
            selected: !_isFoodToAlcohol,
            onTap: () => setState(() => _isFoodToAlcohol = false),
          ),
        ],
      ),
    );
  }

  Widget _toggleOption({
    required String label,
    required bool selected,
    required VoidCallback onTap,
  }) {
    return Expanded(
      child: GestureDetector(
        onTap: onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          margin: const EdgeInsets.all(4),
          decoration: BoxDecoration(
            color: selected ? _gold : Colors.transparent,
            borderRadius: BorderRadius.circular(10),
          ),
          alignment: Alignment.center,
          child: Text(
            label,
            style: TextStyle(
              color: selected ? _bg : Colors.white.withOpacity(0.4),
              fontSize: 13,
              fontWeight: selected ? FontWeight.w700 : FontWeight.w400,
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildLabel() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          _isFoodToAlcohol ? 'Что у вас на столе?' : 'Какой напиток хотите?',
          style: const TextStyle(
            color: Colors.white,
            fontSize: 20,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 4),
        Text(
          _isFoodToAlcohol
              ? 'Опишите подробнее — чем точнее, тем лучше подборка'
              : 'Укажите напиток — подберем закуски и блюда',
          style: TextStyle(
            color: Colors.white.withOpacity(0.35),
            fontSize: 13,
          ),
        ),
      ],
    );
  }

  Widget _buildInputField() {
    return Container(
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        color: _card,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: _gold.withOpacity(0.28),
          width: 1.5,
          strokeAlign: BorderSide.strokeAlignInside,
        ),
      ),
      child: TextField(
        controller: _controller,
        style: const TextStyle(color: Colors.white, fontSize: 15),
        maxLines: 4,
        minLines: 4,
        decoration: InputDecoration(
          hintText: _isFoodToAlcohol
              ? 'Например: говяжий стейк medium rare, рамен с курицей, сырная тарелка с виноградом...'
              : 'Например: красное сухое вино, односолодовый виски, русская водка...',
          hintStyle: TextStyle(
            color: Colors.white.withOpacity(0.22),
            fontSize: 14,
          ),
          contentPadding: const EdgeInsets.all(18),
          border: InputBorder.none,
        ),
      ),
    );
  }

  Widget _buildHints() {
    final hints = _isFoodToAlcohol
        ? ['🥩 Говяжий стейк', '🍣 Суши', '🫕 Рамен с курицей', '🧀 Сырная тарелка']
        : ['🍷 Красное вино', '🥃 Виски', '🍺 Пиво', '🥂 Шампанское'];

    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: hints.map((hint) {
        return GestureDetector(
          onTap: () => setState(() {
            _controller.text = hint.substring(3);
          }),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
            decoration: BoxDecoration(
              color: _card,
              borderRadius: BorderRadius.circular(20),
              border: Border.all(color: Colors.white.withOpacity(0.08)),
            ),
            child: Text(
              hint,
              style: TextStyle(
                color: Colors.white.withOpacity(0.45),
                fontSize: 13,
              ),
            ),
          ),
        );
      }).toList(),
    );
  }

  Widget _buildBudgetSelector() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Бюджет',
          style: TextStyle(
            color: Colors.white.withOpacity(0.5),
            fontSize: 13,
            fontWeight: FontWeight.w500,
            letterSpacing: 0.5,
          ),
        ),
        const SizedBox(height: 10),
        Row(
          children: List.generate(3, (i) {
            final selected = _budgetIndex == i;
            return Expanded(
              child: GestureDetector(
                onTap: () {
                  setState(() => _budgetIndex = i);
                  _saveBudget(i);
                },
                child: AnimatedContainer(
                  duration: const Duration(milliseconds: 200),
                  margin: EdgeInsets.only(right: i < 2 ? 8 : 0),
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  decoration: BoxDecoration(
                    color: selected ? _gold.withOpacity(0.15) : _card,
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(
                      color: selected ? _gold : Colors.white.withOpacity(0.06),
                      width: selected ? 1.5 : 1,
                    ),
                  ),
                  child: Column(
                    children: [
                      Text(
                        _budgetIcons[i],
                        style: const TextStyle(fontSize: 16),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        _budgetLabels[i],
                        style: TextStyle(
                          color: selected ? _gold : Colors.white.withOpacity(0.4),
                          fontSize: 12,
                          fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            );
          }),
        ),
      ],
    );
  }

  Future<void> _onPair() async {
    final dish = _controller.text.trim();
    if (dish.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(_isFoodToAlcohol ? 'Введите блюдо' : 'Введите напиток'),
          backgroundColor: Colors.red.shade800,
          behavior: SnackBarBehavior.floating,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
          margin: const EdgeInsets.only(bottom: 80, left: 16, right: 16),
        ),
      );
      return;
    }

    if (_navigating) return;
    setState(() => _navigating = true);

    HapticFeedback.mediumImpact();
    // Снимаем фокус с TextField перед переходом на Result/Paywall.
    // Иначе при возврате назад (Navigator.pop) клавиатура всплывает заново
    // и кнопка "Подобрать" прыгает вверх.
    FocusScope.of(context).unfocus();

    if (mounted) {
      await Navigator.push(
        context,
        PageRouteBuilder(
          pageBuilder: (_, __, ___) => ResultScreen(
            dish: dish,
            mode: _isFoodToAlcohol ? 'food_to_alcohol' : 'alcohol_to_food',
            budget: _isFoodToAlcohol ? _budgetKeys[_budgetIndex] : 'medium',
          ),
          transitionsBuilder: (_, animation, __, child) =>
              FadeTransition(opacity: animation, child: child),
          transitionDuration: const Duration(milliseconds: 220),
        ),
      );
    }

    if (mounted) setState(() => _navigating = false);
  }

  Widget _buildButton() {
    return SizedBox(
      width: double.infinity,
      height: 54,
      child: ElevatedButton(
        onPressed: _navigating ? null : _onPair,
        style: ElevatedButton.styleFrom(
          backgroundColor: _gold,
          foregroundColor: _bg,
          disabledBackgroundColor: _gold.withOpacity(0.5),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
          elevation: 0,
        ),
        child: Text(
          _isFoodToAlcohol ? 'Подобрать напиток' : 'Подобрать блюда',
          style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700, letterSpacing: 0.3),
        ),
      ),
    );
  }
}
