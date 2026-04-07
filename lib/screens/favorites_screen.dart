import 'package:flutter/material.dart';
import '../models/pairing_result.dart';
import '../services/api_service.dart';
import 'result_screen.dart';

class FavoritesScreen extends StatefulWidget {
  final VoidCallback? onGoHome;
  const FavoritesScreen({super.key, this.onGoHome});

  @override
  State<FavoritesScreen> createState() => _FavoritesScreenState();
}

class _FavoritesScreenState extends State<FavoritesScreen> {
  static const _gold = Color(0xFFC9A84C);
  static const _bg = Color(0xFF0D0D0D);
  static const _card = Color(0xFF1A1A1A);
  // Material Design red — стандартный цвет для destructive actions
  static const _deleteRed = Color(0xFFE53935);

  List<PairingResponse> _favorites = [];
  bool _loading = true;

  // Прогресс свайпа для каждой карточки (key Dismissible → 0.0..1.0).
  // Используется для анимации иконки корзины — scale растёт по мере свайпа.
  // Источник данных: Dismissible.onUpdate(DismissUpdateDetails details) → details.progress
  final Map<Key, double> _swipeProgress = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final data = await ApiService.getFavorites();
    setState(() {
      _favorites = data;
      _loading = false;
    });
  }

  void _remove(int index) {
    final removedItem = _favorites[index];
    final removedIndex = index;
    setState(() => _favorites.removeAt(index));

    bool undone = false;

    // Берём локальный ScaffoldMessenger один раз и используем его для всех
    // операций — чтобы не зависеть от состояния context после rebuild.
    final messenger = ScaffoldMessenger.of(context);
    messenger.removeCurrentSnackBar();

    final controller = messenger.showSnackBar(
      SnackBar(
        content: const Text(
          'Удалено',
          style: TextStyle(color: Colors.white, fontWeight: FontWeight.w500),
        ),
        backgroundColor: _card,
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: BorderSide(color: Colors.white.withOpacity(0.1)),
        ),
        margin: const EdgeInsets.fromLTRB(20, 0, 20, 80),
        duration: const Duration(seconds: 3),
        elevation: 0,
        action: SnackBarAction(
          label: 'Отменить',
          textColor: _gold,
          onPressed: () {
            undone = true;
            setState(() {
              if (removedIndex <= _favorites.length) {
                _favorites.insert(removedIndex, removedItem);
              } else {
                _favorites.add(removedItem);
              }
            });
          },
        ),
      ),
    );

    controller.closed.then((reason) {
      // Если пользователь не нажал "Отменить" — фиксируем удаление в БД
      if (!undone && removedItem.id != null) {
        ApiService.removeFavorite(removedItem.id!);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    // ScaffoldMessenger обёрнут локально вокруг этой вкладки чтобы SnackBar
    // "Удалено · Отменить" жил только внутри Избранного, а не следовал за
    // пользователем по другим вкладкам и не зависал. Без этой обёртки SnackBar
    // привязывается к root MaterialApp и не управляется свайпами по нав-бару.
    return ScaffoldMessenger(
      child: Scaffold(
        backgroundColor: _bg,
        appBar: AppBar(
          backgroundColor: _bg,
          surfaceTintColor: Colors.transparent,
          title: const Text(
            'Избранное',
            style: TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w600),
          ),
        ),
        body: _loading
            ? const Center(child: CircularProgressIndicator(color: _gold))
            : _favorites.isEmpty
                ? _buildEmpty()
                : _buildList(),
      ),
    );
  }

  Widget _buildEmpty() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(40),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(Icons.star_rounded, color: _gold, size: 56),
            const SizedBox(height: 20),
            const Text(
              'Здесь будут ваши дуэты',
              style: TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w600),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 10),
            Text(
              'Найдите дуэт и сохраните его — он появится здесь',
              style: TextStyle(color: Colors.white.withOpacity(0.4), fontSize: 14, height: 1.5),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 32),
            SizedBox(
              width: double.infinity,
              height: 50,
              child: ElevatedButton(
                onPressed: widget.onGoHome,
                style: ElevatedButton.styleFrom(
                  backgroundColor: _gold,
                  foregroundColor: _bg,
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                  elevation: 0,
                ),
                child: const Text('Подобрать первый дуэт', style: TextStyle(fontWeight: FontWeight.w700)),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildList() {
    return ListView.builder(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
      itemCount: _favorites.length,
      itemBuilder: (context, index) {
        final item = _favorites[index];
        return _buildCard(item, index);
      },
    );
  }

  Widget _buildCard(PairingResponse item, int index) {
    final firstResult = item.results.isNotEmpty ? item.results.first : null;
    final cardKey = Key('fav_${item.dish}_${item.budget}_${item.createdAt.millisecondsSinceEpoch}');
    // 0.0 пока пользователь не свайпает, растёт до 1.0 при полном свайпе
    final progress = _swipeProgress[cardKey] ?? 0.0;
    // Scale корзинки: от 0.7 в покое до 1.2 в полном свайпе. Линейная интерполяция.
    final iconScale = 0.7 + (progress.clamp(0.0, 1.0) * 0.5);
    return Dismissible(
      key: cardKey,
      direction: DismissDirection.endToStart,
      onUpdate: (details) {
        // Триггер только при заметном изменении — снижает количество ребилдов
        final newProgress = details.progress;
        final oldProgress = _swipeProgress[cardKey] ?? 0.0;
        if ((newProgress - oldProgress).abs() > 0.02) {
          setState(() => _swipeProgress[cardKey] = newProgress);
        }
      },
      background: Container(
        margin: const EdgeInsets.only(bottom: 12),
        decoration: BoxDecoration(
          color: _card,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Align(
          alignment: Alignment.centerRight,
          child: Container(
            width: 80,
            decoration: const BoxDecoration(
              color: _deleteRed,
              borderRadius: BorderRadius.only(
                topRight: Radius.circular(14),
                bottomRight: Radius.circular(14),
              ),
            ),
            child: Center(
              child: AnimatedScale(
                scale: iconScale,
                duration: const Duration(milliseconds: 80),
                curve: Curves.easeOut,
                child: const Icon(
                  Icons.delete_rounded,
                  color: Colors.white,
                  size: 24,
                ),
              ),
            ),
          ),
        ),
      ),
      onDismissed: (_) {
        _swipeProgress.remove(cardKey);
        _remove(index);
      },
      child: GestureDetector(
        onTap: () => Navigator.push(
          context,
          MaterialPageRoute(builder: (_) => ResultScreen(response: item)),
        ),
        child: Container(
          margin: const EdgeInsets.only(bottom: 12),
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: _card,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: Colors.white.withOpacity(0.06)),
          ),
          child: Row(
            children: [
              Text(
                firstResult?.resolvedEmoji ?? '🍷',
                style: const TextStyle(fontSize: 32),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      item.dish,
                      style: const TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.w600),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: 4),
                    Text(
                      firstResult != null ? '${firstResult.alcoholType} · ${firstResult.brand}' : '',
                      style: TextStyle(color: Colors.white.withOpacity(0.4), fontSize: 13),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                ),
              ),
              const Icon(Icons.chevron_right_rounded, color: Colors.white24, size: 20),
            ],
          ),
        ),
      ),
    );
  }
}
