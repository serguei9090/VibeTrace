package com.example.shop;

public class CheckoutService {
    private PaymentGateway gateway;

    public CheckoutService() {
        this.gateway = new PaymentGateway("m_123");
    }

    public boolean processOrder(double total) {
        return this.gateway.charge(total);
    }
}
