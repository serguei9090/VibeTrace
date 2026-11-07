package payment

import "fmt"

type Service struct {
    Endpoint string
}

func NewService(endpoint string) *Service {
    return &Service{Endpoint: endpoint}
}

func (s *Service) Charge(amount float64) error {
    if amount <= 0 {
        return fmt.Errorf("invalid amount")
    }
    return nil
}
