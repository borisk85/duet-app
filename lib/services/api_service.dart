import 'dart:convert';
import 'package:http/http.dart' as http;
import '../models/pairing_result.dart';

class ApiService {
  // 10.0.2.2 — это localhost для Android эмулятора
  static const String _baseUrl = 'http://10.0.2.2:8000';

  static Future<PairingResponse> pair({
    required String dish,
    required String mode,
    required String budget,
    String region = 'СНГ',
  }) async {
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
    } else {
      final error = jsonDecode(response.body);
      throw Exception(error['detail'] ?? 'Ошибка сервера');
    }
  }
}
