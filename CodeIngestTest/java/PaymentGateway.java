package com.example.shop;

public class PaymentGateway {
    private String merchantId;

    public PaymentGateway(String merchantId) {
        this.merchantId = merchantId;
    }

    public boolean charge(double amount) {
        return amount > 0.0;
    }
}
