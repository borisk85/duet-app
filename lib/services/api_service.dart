import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import '../models/pairing_result.dart';

class ApiService {
  static const String _baseUrl = 'https://duet-app-production.up.railway.app';

  static Future<PairingResponse> pair({
    required String dish,
    required String mode,
    required String budget,
  }) async {
    final prefs = await SharedPreferences.getInstance();
    final region = prefs.getString('region') ?? 'СНГ';

    final response = await http.post(
      Uri.parse('$_baseUrl/pair'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'dish': dish,
        'mode': mode,
        'budget': budget,
        'region': region,
      }),
    ).timeout(const Duration(seconds: 30));

    if (response.statusCode == 200) {
      final data = jsonDecode(utf8.decode(response.bodyBytes));
      return PairingResponse.fromJson(data);
    } else if (response.statusCode == 429) {
      throw Exception('Достигнут дневной лимит. Обновите до Premium для безлимита.');
    } else {
      try {
        final error = jsonDecode(response.body);
        throw Exception(error['detail'] ?? 'Ошибка сервера');
      } catch (_) {
        throw Exception('Сервис временно недоступен. Попробуйте через минуту.');
      }
    }
  }

  static Stream<String> pairStream({
    required String dish,
    required String mode,
    required String budget,
  }) async* {
    final prefs = await SharedPreferences.getInstance();
    final region = prefs.getString('region') ?? 'СНГ';

    final client = http.Client();
    try {
      final request = http.Request('POST', Uri.parse('$_baseUrl/pair/stream'));
      request.headers['Content-Type'] = 'application/json';
      request.body = jsonEncode({
        'dish': dish,
        'mode': mode,
        'budget': budget,
        'region': region,
      });

      final streamed = await client
          .send(request)
          .timeout(const Duration(seconds: 30));

      if (streamed.statusCode == 429) {
        throw Exception('Достигнут дневной лимит. Обновите до Premium для безлимита.');
      }
      if (streamed.statusCode != 200) {
        throw Exception('Сервис временно недоступен. Попробуйте через минуту.');
      }

      await for (final chunk in streamed.stream.transform(utf8.decoder)) {
        yield chunk;
      }
    } finally {
      client.close();
    }
  }
}
